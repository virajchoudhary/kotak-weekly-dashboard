import io
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

import weekly_engine
from config import LOCAL_DEV_ORIGIN_REGEX, Settings
from db import init_db
from observability import (
    ProxyIdentityMiddleware,
    RequestContextMiddleware,
    audit,
    configure_logging,
    get_logger,
    get_request_id,
    sanitize_filename,
)


UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
IDENTITY_EXEMPT_PATHS = ("/", "/healthz", "/readyz")


def server_error(action: str) -> HTTPException:
    """Log the active exception server-side and return a sanitized 500."""
    request_id = get_request_id()
    get_logger().exception("unhandled_error action=%s request_id=%s", action, request_id)
    return HTTPException(
        status_code=500,
        detail=f"Internal server error. Reference ID: {request_id}.",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    configure_logging(settings.log_level)
    if settings.db_path is not None:
        weekly_engine.DB_PATH = settings.db_path
    logger = get_logger()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        logger.info(json.dumps({"event": "startup", "environment": settings.environment}))
        yield

    app = FastAPI(
        title="Weekly AMFI Dashboard API",
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware is added inner-first; the last added (CORS) is the outermost,
    # so every response (including auth/host rejections) gets CORS headers and a
    # request id. Order outer -> inner: CORS, RequestContext, TrustedHost, Identity.
    app.add_middleware(
        ProxyIdentityMiddleware,
        enabled=settings.is_production and settings.require_proxy_identity,
        identity_header=settings.identity_header,
        exempt_paths=IDENTITY_EXEMPT_PATHS,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.effective_trusted_hosts)
    app.add_middleware(RequestContextMiddleware, identity_header=settings.identity_header)
    cors_options = {
        "allow_origins": list(settings.allowed_origins),
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-ID"],
    }
    if not settings.is_production:
        cors_options["allow_origin_regex"] = LOCAL_DEV_ORIGIN_REGEX
    app.add_middleware(CORSMiddleware, **cors_options)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        checks: dict[str, str] = {}
        ready = True
        try:
            conn = weekly_engine.get_db_connection()
            try:
                conn.execute("SELECT 1")
                conn.execute("SELECT COUNT(*) FROM periods")
            finally:
                conn.close()
            checks["database"] = "ok"
        except Exception:
            ready = False
            checks["database"] = "unavailable"
            logger.exception("readiness_db_check_failed request_id=%s", get_request_id())
        if settings.is_production:
            checks["db_path"] = "configured" if settings.db_path is not None else "missing"
            checks["allowed_origins"] = "configured" if settings.allowed_origins else "missing"
            checks["trusted_hosts"] = "configured" if settings.trusted_hosts else "missing"
            if settings.db_path is None or not settings.allowed_origins or not settings.trusted_hosts:
                ready = False
        if not ready:
            return JSONResponse(status_code=503, content={"status": "not ready", "checks": checks})
        return {"status": "ready", "checks": checks}

    @app.get("/")
    def read_root():
        if settings.enable_docs:
            return RedirectResponse(url="/docs")
        return {"status": "ok"}

    @app.get("/dashboard-data")
    @app.get("/api/metrics")
    def get_metrics(
        financial_year: str = Query(None),
        fy: str = Query(None),
        period_key: str = Query(None),
    ):
        try:
            target_fy = financial_year or fy or weekly_engine.latest_financial_year()
            payload = weekly_engine.dashboard_payload(fy=target_fy, period_key=period_key)
            audit("dashboard.read", fy=target_fy, period_key=period_key)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            raise server_error("dashboard.read")

    @app.post("/upload")
    @app.post("/api/upload")
    async def upload(file: UploadFile):
        validate_upload_filename(file.filename)
        safe_name = sanitize_filename(file.filename)
        try:
            uploaded = await read_limited_upload(file, settings.max_upload_bytes)
            period, warnings = weekly_engine.process_upload(uploaded, file.filename)
            payload = weekly_engine.dashboard_payload(
                fy=period["financialYear"],
                warnings=warnings,
                upload_period=period,
            )
            audit("upload", filename=safe_name, period=period.get("periodKey"), fy=period.get("financialYear"))
            return payload
        except HTTPException:
            raise
        except ValueError as exc:
            audit("upload", outcome="rejected", filename=safe_name, reason=str(exc))
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            audit("upload", outcome="error", filename=safe_name)
            raise server_error("upload")

    @app.get("/api/archives")
    def get_archives():
        try:
            archives = weekly_engine.list_archives()
            audit("archive.read", count=len(archives))
            return archives
        except Exception:
            raise server_error("archive.read")

    @app.get("/api/template-status")
    def template_status(financial_year: str = Query(None), fy: str = Query(None)):
        try:
            target_fy = financial_year or fy or weekly_engine.latest_financial_year()
            periods = weekly_engine.periods_for_fy(target_fy) if target_fy else []
            return {
                "templateVersion": weekly_engine.WORKBOOK_TEMPLATE_VERSION,
                "financialYear": target_fy,
                "monthlyBlockCount": len(periods),
                "hasFinalYtdSummaryBlock": True,
                "monthBlockHeadings": ["AUM", "Gross Sales", "Net Sales"],
                "finalBlockHeadings": ["AUM", "YTD Gross Sales", "YTD Net Sales"],
            }
        except Exception:
            raise server_error("template.status")

    @app.get("/api/download-summary")
    def download_summary(financial_year: str = Query(None), fy: str = Query(None), period_key: str = Query(None)):
        try:
            target_fy = financial_year or fy
            excel_bytes = weekly_engine.compile_summary_workbook(period_key=period_key, fy=target_fy)
            period = weekly_engine.resolve_period(period_key=period_key, fy=target_fy)
            filename = f"AMFI Weekly Summary - {period['period_label'].replace(' ', '_')}.xlsx"
            headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
            audit("download.summary", fy=target_fy, period_key=period_key)
            return StreamingResponse(
                io.BytesIO(excel_bytes),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers=headers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            raise server_error("download.summary")

    @app.get("/api/download-mom")
    def download_mom(financial_year: str = Query(None), fy: str = Query(None)):
        target_fy = financial_year or fy
        try:
            excel_bytes = weekly_engine.compile_mom_workbook(fy=target_fy)
            fy_label = target_fy or weekly_engine.latest_financial_year() or "latest"
            filename = f"AMFI Weekly MoM YTD - FY {fy_label}.xlsx"
            headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
            audit("download.mom", fy=fy_label)
            return StreamingResponse(
                io.BytesIO(excel_bytes),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers=headers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            raise server_error("download.mom")

    @app.get("/api/download")
    def download_default(financial_year: str = Query(None), fy: str = Query(None)):
        return download_mom(financial_year=financial_year, fy=fy)

    return app


def validate_upload_filename(filename: str | None) -> None:
    if not filename or not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a .xlsx workbook.")


async def read_limited_upload(file: UploadFile, max_upload_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded file exceeds the {format_bytes(max_upload_bytes)} limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def format_bytes(value: int) -> str:
    mib = value / (1024 * 1024)
    return f"{mib:g} MiB"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
