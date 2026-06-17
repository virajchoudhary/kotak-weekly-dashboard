import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_IDENTITY_HEADER = "X-Forwarded-User"
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _split_origins(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    origins = []
    for item in value.split(","):
        origin = item.strip().rstrip("/")
        if origin:
            origins.append(origin)
    return tuple(dict.fromkeys(origins))


def _split_hosts(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    hosts = []
    for item in value.split(","):
        host = item.strip()
        if host:
            hosts.append(host)
    return tuple(dict.fromkeys(hosts))


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_positive_int(value: str | None, default: int, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return parsed


def _parse_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value.strip()).expanduser()


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    allowed_origins: tuple[str, ...] = ()
    enable_docs: bool = True
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    trusted_hosts: tuple[str, ...] = ()
    require_proxy_identity: bool = False
    identity_header: str = DEFAULT_IDENTITY_HEADER
    db_path: Path | None = None
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def effective_trusted_hosts(self) -> list[str]:
        """Hosts for TrustedHostMiddleware. Permissive in dev, explicit in prod."""
        if self.trusted_hosts:
            return list(self.trusted_hosts)
        if self.is_production:
            return []  # validate() guarantees this state never reaches the app
        return ["*"]

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        source = environ if environ is not None else os.environ
        environment = (source.get("ENVIRONMENT") or "development").strip().lower()
        is_production = environment == "production"
        enable_docs = _parse_bool(source.get("ENABLE_DOCS"), default=not is_production)
        identity_header = (source.get("IDENTITY_HEADER") or DEFAULT_IDENTITY_HEADER).strip() or DEFAULT_IDENTITY_HEADER
        db_path = _parse_path(source.get("WEEKLY_DB_PATH") or source.get("DB_PATH"))
        log_level = (source.get("LOG_LEVEL") or "INFO").strip() or "INFO"
        return cls(
            environment=environment,
            allowed_origins=_split_origins(source.get("ALLOWED_ORIGINS")),
            enable_docs=enable_docs,
            max_upload_bytes=_parse_positive_int(
                source.get("MAX_UPLOAD_BYTES"),
                DEFAULT_MAX_UPLOAD_BYTES,
                "MAX_UPLOAD_BYTES",
            ),
            trusted_hosts=_split_hosts(source.get("TRUSTED_HOSTS")),
            require_proxy_identity=_parse_bool(source.get("REQUIRE_PROXY_IDENTITY"), default=False),
            identity_header=identity_header,
            db_path=db_path,
            log_level=log_level,
        )

    def validate(self) -> None:
        if not self.is_production:
            return
        if not self.allowed_origins:
            raise RuntimeError("ALLOWED_ORIGINS must be set when ENVIRONMENT=production.")
        for origin in self.allowed_origins:
            if origin == "*":
                raise RuntimeError(
                    "Wildcard '*' is not permitted in ALLOWED_ORIGINS when ENVIRONMENT=production."
                )
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise RuntimeError(
                    f"ALLOWED_ORIGINS entry '{origin}' must use an http:// or https:// scheme in production."
                )
        if not self.trusted_hosts:
            raise RuntimeError("TRUSTED_HOSTS must be set when ENVIRONMENT=production.")
        if "*" in self.trusted_hosts:
            raise RuntimeError(
                "Wildcard '*' is not permitted in TRUSTED_HOSTS when ENVIRONMENT=production."
            )
        if self.db_path is None:
            raise RuntimeError("WEEKLY_DB_PATH (or DB_PATH) must be set when ENVIRONMENT=production.")
