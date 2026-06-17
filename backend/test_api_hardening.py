import asyncio
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main
import weekly_engine
from config import Settings


def workbook_bytes(workbook: Workbook) -> bytes:
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def workbook_without_summary() -> bytes:
    workbook = Workbook()
    workbook.active.title = "Not Summary"
    return workbook_bytes(workbook)


def workbook_with_invalid_summary_structure() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Summary"
    worksheet["C3"] = "Summary-  01ST To 31ST MAY'26"
    return workbook_bytes(workbook)


def corrupted_ooxml_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types>")
        archive.writestr("xl/workbook.xml", "<workbook>")
    return output.getvalue()


def multipart_file_body(filename: str, content: bytes) -> tuple[bytes, dict[str, str]]:
    boundary = "----codex-upload-boundary"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return body, {
        "content-type": f"multipart/form-data; boundary={boundary}",
        "content-length": str(len(body)),
    }


async def request_asgi(app, method: str, target: str, body: bytes = b"", headers: dict[str, str] | None = None):
    parsed = urlsplit(target)
    request_headers = {"host": "testserver"}
    if headers:
        request_headers.update(headers)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in request_headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    messages = []
    sent_body = False

    async def receive():
        nonlocal sent_body
        if sent_body:
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_headers = {}
    for message in messages:
        if message["type"] == "http.response.start":
            for key, value in message.get("headers", []):
                response_headers.setdefault(key.decode("latin-1"), []).append(value.decode("latin-1"))
    response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, response_headers, response_body


def request(app, method: str, target: str, body: bytes = b"", headers: dict[str, str] | None = None):
    return asyncio.run(request_asgi(app, method, target, body=body, headers=headers))


class ApiHardeningTest(unittest.TestCase):
    def setUp(self):
        self.original_db = weekly_engine.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        weekly_engine.DB_PATH = Path(self.temp_dir.name) / "weekly_test.db"
        self.db_path = weekly_engine.DB_PATH
        self.fixture = Path(__file__).resolve().parents[1] / "data" / "uploads" / "Weekly Inflow And AUM-1st-31st May'26.xlsx"
        self.upload_bytes = self.fixture.read_bytes()
        weekly_engine.init_db()

    def tearDown(self):
        weekly_engine.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    def make_app(self, **overrides):
        settings = Settings(**overrides)
        return main.create_app(settings)

    def db_counts(self) -> tuple[int, int, int]:
        conn = weekly_engine.get_db_connection()
        try:
            return (
                conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM summary_rows").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM scheme_rows").fetchone()[0],
            )
        finally:
            conn.close()

    def post_upload(self, app, filename: str, content: bytes):
        body, headers = multipart_file_body(filename, content)
        return request(app, "POST", "/upload", body=body, headers=headers)

    def assert_failed_upload_unchanged(self, filename: str, content: bytes, expected_status: int = 400, **settings):
        app = self.make_app(**settings)
        before = self.db_counts()
        status, headers, body = self.post_upload(app, filename, content)
        self.assertEqual(status, expected_status, body.decode("utf-8", errors="replace"))
        self.assertEqual(before, self.db_counts())
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("detail", payload)
        return payload

    def test_valid_xlsx_upload_succeeds(self):
        app = self.make_app()
        status, headers, body = self.post_upload(app, self.fixture.name, self.upload_bytes)
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["financialYear"], "2026-2027")
        self.assertIn("summary", payload)
        self.assertIn("timeSeries", payload)
        self.assertIn("categorySummary", payload)
        self.assertIn("schemeSummary", payload)

    def test_xls_upload_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged("weekly.xls", self.upload_bytes)
        self.assertIn(".xlsx", payload["detail"])

    def test_fake_xlsx_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged("weekly.xlsx", b"not an excel file")
        self.assertIn("valid .xlsx", payload["detail"])

    def test_corrupted_workbook_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged("weekly.xlsx", corrupted_ooxml_bytes())
        self.assertIn("valid .xlsx", payload["detail"])

    def test_missing_summary_sheet_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged("weekly.xlsx", workbook_without_summary())
        self.assertIn("Summary", payload["detail"])

    def test_invalid_summary_structure_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged("weekly.xlsx", workbook_with_invalid_summary_structure())
        self.assertIn("Summary sheet", payload["detail"])

    def test_oversize_upload_is_rejected_cleanly_and_does_not_change_db(self):
        payload = self.assert_failed_upload_unchanged(
            self.fixture.name,
            self.upload_bytes,
            expected_status=413,
            max_upload_bytes=10,
        )
        self.assertIn("limit", payload["detail"])

    def test_production_docs_are_disabled(self):
        app = self.make_app(
            environment="production",
            allowed_origins=("https://dashboard.internal",),
            trusted_hosts=("testserver",),
            db_path=self.db_path,
            enable_docs=False,
        )
        status, headers, body = request(app, "GET", "/docs")
        self.assertEqual(status, 404)

    def test_production_cors_rejects_unknown_origin(self):
        app = self.make_app(
            environment="production",
            allowed_origins=("https://dashboard.internal",),
            trusted_hosts=("testserver",),
            db_path=self.db_path,
            enable_docs=False,
        )
        status, headers, body = request(
            app,
            "OPTIONS",
            "/dashboard-data",
            headers={
                "origin": "https://unknown.example",
                "access-control-request-method": "GET",
            },
        )
        self.assertEqual(status, 400)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_existing_read_and_download_routes_still_work(self):
        app = self.make_app()
        for path in ("/dashboard-data", "/api/archives", "/api/template-status"):
            status, headers, body = request(app, "GET", path)
            self.assertEqual(status, 200, f"{path}: {body!r}")

        status, headers, body = request(app, "GET", "/api/download-mom")
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        self.assertGreater(len(body), 0)
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers["content-type"][0])


if __name__ == "__main__":
    unittest.main()
