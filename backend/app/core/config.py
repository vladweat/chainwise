from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://chainwise:change_this_in_prod@localhost:5432/chainwise_db",
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

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
