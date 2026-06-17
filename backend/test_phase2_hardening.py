import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main
import weekly_engine
from config import Settings
from test_api_hardening import request


def raising(message: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(message)
    return _raise


class Phase2HardeningTest(unittest.TestCase):
    def setUp(self):
        self.original_db = weekly_engine.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        weekly_engine.DB_PATH = Path(self.temp_dir.name) / "weekly_test.db"
        self.db_path = weekly_engine.DB_PATH
        weekly_engine.init_db()

    def tearDown(self):
        weekly_engine.DB_PATH = self.original_db
        self.temp_dir.cleanup()

    def dev_app(self):
        return main.create_app(Settings(log_level="CRITICAL"))

    def prod_settings(self, **overrides):
        base = dict(
            environment="production",
            allowed_origins=("https://dashboard.internal",),
            trusted_hosts=("testserver",),
            db_path=self.db_path,
            enable_docs=False,
            log_level="CRITICAL",
        )
        base.update(overrides)
        return Settings(**base)

    # --- A. Production CORS validation -----------------------------------
    def test_production_rejects_wildcard_origin(self):
        with self.assertRaises(RuntimeError):
            Settings(
                environment="production", allowed_origins=("*",),
                trusted_hosts=("h.example",), db_path=self.db_path,
            ).validate()

    def test_production_rejects_wildcard_origin_from_env(self):
        settings = Settings.from_env({
            "ENVIRONMENT": "production",
            "ALLOWED_ORIGINS": "*",
            "TRUSTED_HOSTS": "dashboard.internal",
            "WEEKLY_DB_PATH": str(self.db_path),
        })
        with self.assertRaises(RuntimeError):
            settings.validate()

    def test_production_rejects_schemeless_origin(self):
        with self.assertRaises(RuntimeError):
            Settings(
                environment="production", allowed_origins=("dashboard.internal",),
                trusted_hosts=("testserver",), db_path=self.db_path,
            ).validate()

    def test_production_requires_trusted_hosts(self):
        with self.assertRaises(RuntimeError):
            Settings(
                environment="production", allowed_origins=("https://x.example",),
                trusted_hosts=(), db_path=self.db_path,
            ).validate()

    def test_production_requires_db_path(self):
        with self.assertRaises(RuntimeError):
            Settings(
                environment="production", allowed_origins=("https://x.example",),
                trusted_hosts=("testserver",), db_path=None,
            ).validate()

    def test_development_validates_and_allows_all_hosts(self):
        settings = Settings()
        settings.validate()  # must not raise
        self.assertEqual(settings.effective_trusted_hosts, ["*"])

    def test_valid_production_settings_validate(self):
        self.prod_settings().validate()  # must not raise

    # --- B. Proxy identity enforcement -----------------------------------
    def test_missing_identity_rejected(self):
        app = main.create_app(self.prod_settings(require_proxy_identity=True))
        status, headers, body = request(app, "GET", "/dashboard-data")
        self.assertEqual(status, 401, body.decode("utf-8", "replace"))

    def test_valid_identity_accepted(self):
        app = main.create_app(self.prod_settings(require_proxy_identity=True))
        status, headers, body = request(
            app, "GET", "/dashboard-data", headers={"x-forwarded-user": "alice"}
        )
        self.assertEqual(status, 200, body.decode("utf-8", "replace"))
        self.assertIn("x-request-id", headers)

    def test_health_is_exempt_from_identity(self):
        app = main.create_app(self.prod_settings(require_proxy_identity=True))
        status, headers, body = request(app, "GET", "/healthz")
        self.assertEqual(status, 200, body.decode("utf-8", "replace"))

    def test_custom_identity_header(self):
        app = main.create_app(
            self.prod_settings(require_proxy_identity=True, identity_header="X-Auth-User")
        )
        missing, _, _ = request(app, "GET", "/dashboard-data", headers={"x-forwarded-user": "alice"})
        self.assertEqual(missing, 401)
        ok, _, _ = request(app, "GET", "/dashboard-data", headers={"x-auth-user": "alice"})
        self.assertEqual(ok, 200)

    def test_development_does_not_require_identity(self):
        app = main.create_app(Settings(require_proxy_identity=True, log_level="CRITICAL"))
        status, _, body = request(app, "GET", "/dashboard-data")
        self.assertEqual(status, 200, body.decode("utf-8", "replace"))

    # --- C. Sanitized server errors --------------------------------------
    def test_metrics_500_is_sanitized(self):
        original = weekly_engine.dashboard_payload
        weekly_engine.dashboard_payload = raising("LEAK_SENTINEL_METRICS")
        try:
            status, headers, body = request(self.dev_app(), "GET", "/dashboard-data")
        finally:
            weekly_engine.dashboard_payload = original
        text = body.decode("utf-8", "replace")
        self.assertEqual(status, 500, text)
        self.assertNotIn("LEAK_SENTINEL_METRICS", text)
        self.assertIn("Reference ID", text)

    def test_archives_500_is_sanitized(self):
        original = weekly_engine.list_archives
        weekly_engine.list_archives = raising("LEAK_SENTINEL_ARCHIVES")
        try:
            status, headers, body = request(self.dev_app(), "GET", "/api/archives")
        finally:
            weekly_engine.list_archives = original
        text = body.decode("utf-8", "replace")
        self.assertEqual(status, 500, text)
        self.assertNotIn("LEAK_SENTINEL_ARCHIVES", text)
        self.assertIn("Reference ID", text)

    def test_download_500_is_sanitized(self):
        original = weekly_engine.compile_mom_workbook
        weekly_engine.compile_mom_workbook = raising("LEAK_SENTINEL_DOWNLOAD")
        try:
            status, headers, body = request(self.dev_app(), "GET", "/api/download-mom")
        finally:
            weekly_engine.compile_mom_workbook = original
        text = body.decode("utf-8", "replace")
        self.assertEqual(status, 500, text)
        self.assertNotIn("LEAK_SENTINEL_DOWNLOAD", text)
        self.assertIn("Reference ID", text)

    # --- D. Trusted host validation --------------------------------------
    def test_untrusted_host_rejected(self):
        app = main.create_app(self.prod_settings(trusted_hosts=("dashboard.internal",)))
        bad, _, _ = request(app, "GET", "/healthz", headers={"host": "evil.example"})
        self.assertEqual(bad, 400)
        good, _, body = request(app, "GET", "/healthz", headers={"host": "dashboard.internal"})
        self.assertEqual(good, 200, body.decode("utf-8", "replace"))

    # --- E. Health / readiness -------------------------------------------
    def test_healthz_ok(self):
        status, headers, body = request(self.dev_app(), "GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_readyz_ready(self):
        status, headers, body = request(self.dev_app(), "GET", "/readyz")
        self.assertEqual(status, 200, body.decode("utf-8", "replace"))
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["checks"]["database"], "ok")

    def test_readyz_db_failure_returns_503(self):
        app = self.dev_app()
        saved = weekly_engine.DB_PATH
        weekly_engine.DB_PATH = Path(self.temp_dir.name) / "missing_dir" / "weekly.db"
        try:
            status, headers, body = request(app, "GET", "/readyz")
        finally:
            weekly_engine.DB_PATH = saved
        self.assertEqual(status, 503, body.decode("utf-8", "replace"))
        self.assertEqual(json.loads(body)["status"], "not ready")


if __name__ == "__main__":
    unittest.main()
