import ipaddress
import sys
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


PRODUCTION_ACCESS_TOKEN_MINUTES = 5
PRODUCTION_ACCESS_TOKEN_MAX_MINUTES = 60
_PLACEHOLDER_MARKERS = (
    "change_this",
    "changeme",
    "change_me",
    "replace_me",
    "example",
    "password",
    "secret",
    "default",
    "test",
    "123456",
    "abcdef",
    "qwerty",
    "secret_key",
    "development",
    "dev_only",
)


class ProductionConfigurationError(RuntimeError):
    """A redacted, actionable production configuration failure."""


def _is_strong_credential(value: str) -> bool:
    normalized = value.strip().casefold()
    normalized_compact = normalized.replace("-", "_")
    return (
        value == value.strip()
        and len(value) >= 32
        and len(set(value)) >= 12
        and not any(marker in normalized_compact for marker in _PLACEHOLDER_MARKERS)
    )


def _is_production_https_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        _ = parsed.port  # Reject malformed ports without exposing the input in an error.
    except ValueError:
        return False

    hostname = hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_unspecified)


def _database_password_is_strong(database_url: str) -> bool:
    try:
        url = make_url(database_url)
        password = url.password
        return url.get_backend_name() == "postgresql" and password is not None and _is_strong_credential(password)
    except Exception:
        return False


def validate_production_launch(settings: "Settings", argv: list[str] | None = None) -> None:
    if settings.environment.strip().casefold() != "production":
        return
    arguments = sys.argv if argv is None else argv
    if any(argument == "--reload" or argument.startswith("--reload=") for argument in arguments):
        raise ProductionConfigurationError(
            "Uvicorn --reload is not allowed when ENVIRONMENT=production; "
            "use docker-compose.production.yml."
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg2://chainwise:change_this_in_prod@localhost:5432/chainwise_db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_api_key: str = Field(default="", alias="QDRANT_API_KEY")
    embedding_model_path: str = Field(default="", alias="EMBEDDING_MODEL_PATH")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    secret_key: str = Field(default="change_this_secret_key_in_prod", alias="SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="ALGORITHM")
    access_token_expire_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    etherscan_api_key: str = Field(default="", alias="ETHERSCAN_API_KEY")
    celery_broker_url: str = Field(default="redis://localhost:6379/0", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://localhost:6379/0", alias="CELERY_RESULT_BACKEND")
    slither_url: str = Field(default="http://slither:8000", alias="SLITHER_URL")
    risk_model_path: str = Field(default="", alias="RISK_MODEL_PATH")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="", alias="OPENAI_MODEL")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")
    rate_limit_analyze_per_minute: int = Field(default=10, alias="RATE_LIMIT_ANALYZE_PER_MINUTE")
    rate_limit_chat_per_minute: int = Field(default=30, alias="RATE_LIMIT_CHAT_PER_MINUTE")
    quota_max_contracts: int = Field(default=20, alias="QUOTA_MAX_CONTRACTS")
    quota_analyze_per_day: int = Field(default=20, alias="QUOTA_ANALYZE_PER_DAY")
    quota_chat_per_day: int = Field(default=100, alias="QUOTA_CHAT_PER_DAY")
    refresh_token_expire_minutes: int = Field(default=10080, alias="REFRESH_TOKEN_EXPIRE_MINUTES")
    cookie_samesite: str = Field(default="lax", alias="COOKIE_SAMESITE")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
        alias="CORS_ORIGINS",
    )
    next_public_api_url: str = Field(default="http://localhost:8001", alias="NEXT_PUBLIC_API_URL")

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.environment.strip().casefold() != "production":
            return self

        errors: list[str] = []
        if not _is_strong_credential(self.secret_key):
            errors.append(
                "SECRET_KEY must be at least 32 non-placeholder characters with at least 12 distinct characters; "
                "generate one with `openssl rand -hex 32`."
            )
        if not _database_password_is_strong(self.database_url):
            errors.append(
                "DATABASE_URL must be a valid PostgreSQL URL with a non-placeholder password of at least "
                "32 characters and 12 distinct characters."
            )
        if not self.cookie_secure:
            errors.append("COOKIE_SECURE must be true in production.")
        if self.cookie_samesite.strip().casefold() not in {"lax", "strict", "none"}:
            errors.append("COOKIE_SAMESITE must be one of: lax, strict, none.")
        elif self.cookie_samesite.strip().casefold() == "none" and not self.cookie_secure:
            errors.append("COOKIE_SAMESITE=none requires COOKIE_SECURE=true.")
        if not (
            PRODUCTION_ACCESS_TOKEN_MINUTES
            <= self.access_token_expire_minutes
            <= PRODUCTION_ACCESS_TOKEN_MAX_MINUTES
        ):
            errors.append("ACCESS_TOKEN_EXPIRE_MINUTES must be between 5 and 60 in production; 15 is recommended.")
        if not self.cors_origin_list() or any(
            not _is_production_https_origin(origin) for origin in self.cors_origin_list()
        ):
            errors.append("CORS_ORIGINS must contain only HTTPS origins without localhost or loopback hosts.")
        if not _is_production_https_origin(self.next_public_api_url.strip()):
            errors.append("NEXT_PUBLIC_API_URL must be an HTTPS origin without a path or localhost/loopback host.")

        if errors:
            raise ProductionConfigurationError("Invalid production configuration: " + " ".join(errors)) from None
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
