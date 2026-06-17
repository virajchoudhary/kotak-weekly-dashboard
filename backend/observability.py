"""Structured logging, request correlation, and lightweight auth/audit middleware.

Pure-ASGI middleware is used (not BaseHTTPMiddleware) so that StreamingResponse
downloads are never buffered and request context propagates cleanly.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar

from starlette.responses import JSONResponse

_LOGGER_NAME = "weekly_amfi"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
identity_var: ContextVar[str | None] = ContextVar("identity", default=None)


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent logger setup. Safe to call once per create_app()."""
    logger = logging.getLogger(_LOGGER_NAME)
    resolved = (level or "INFO").upper()
    if logger.handlers:
        logger.setLevel(resolved)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(resolved)
    logger.propagate = False


def get_request_id() -> str:
    return request_id_var.get()


def get_identity() -> str | None:
    return identity_var.get()


def sanitize_filename(filename: str | None) -> str | None:
    """Strip any path components and cap length, for safe audit logging."""
    if not filename:
        return None
    base = filename.replace("\\", "/").split("/")[-1].strip()
    if len(base) > 128:
        base = base[:128]
    return base or None


def audit(action: str, outcome: str = "success", **fields) -> None:
    """Emit one structured audit record. Never include workbook contents."""
    record = {
        "event": "audit",
        "action": action,
        "outcome": outcome,
        "request_id": get_request_id(),
        "identity": get_identity(),
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    get_logger().info(json.dumps(record, default=str))


class RequestContextMiddleware:
    """Assigns/propagates a request id, captures identity, emits an access log."""

    def __init__(self, app, identity_header: str = "X-Forwarded-User"):
        self.app = app
        self._identity_header = identity_header.lower().encode("latin-1")
        self._logger = get_logger()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id")
        request_id = (incoming.decode("latin-1").strip() if incoming else "") or uuid.uuid4().hex
        identity_raw = headers.get(self._identity_header)
        identity = identity_raw.decode("latin-1").strip() if identity_raw else None

        rid_token = request_id_var.set(request_id)
        ident_token = identity_var.set(identity or None)
        status_holder = {"status": 500}
        started = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self._logger.info(json.dumps({
                "event": "access",
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": status_holder["status"],
                "duration_ms": duration_ms,
                "request_id": request_id,
                "identity": identity or None,
            }, default=str))
            identity_var.reset(ident_token)
            request_id_var.reset(rid_token)


class ProxyIdentityMiddleware:
    """Reject requests lacking a proxy-injected identity header.

    Only active when ``enabled`` is True (production + REQUIRE_PROXY_IDENTITY).
    OPTIONS preflight and exempt paths (health/readiness/root) are always allowed.

    SAFETY: this trusts ``identity_header`` blindly. It is safe ONLY behind a
    trusted reverse proxy/SSO that strips any client-supplied copy of the header
    before injecting the authenticated identity. See DEPLOYMENT.md.
    """

    def __init__(self, app, enabled: bool = False,
                 identity_header: str = "X-Forwarded-User", exempt_paths=()):
        self.app = app
        self.enabled = enabled
        self._identity_header = identity_header.lower().encode("latin-1")
        self.exempt_paths = set(exempt_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "OPTIONS" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(self._identity_header)
        if not raw or not raw.decode("latin-1").strip():
            response = JSONResponse(
                {"detail": "Proxy-authenticated identity required."},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
