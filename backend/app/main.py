import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import redis
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.proxy import apply_internal_no_proxy

apply_internal_no_proxy()

from app.api.auth import router as auth_router
from app.api.contracts import router as contracts_router
from app.api.history import router as history_router
from app.core.config import get_settings, validate_production_launch
from app.core.csrf import CsrfCookieMiddleware
from app.core.database import check_postgres
from app.core.request_limits import UploadRequestSizeLimitMiddleware
from app.core.sentry import init_sentry
from app.utils.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)
init_sentry()
logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_launch(settings)
    try:
        _run_migrations()
        logger.info("Database migrations applied")
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        raise
    yield


app = FastAPI(
    title="ChainWise API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(contracts_router, prefix="/api/v1")
app.include_router(history_router, prefix="/api/v1")

app.add_middleware(CsrfCookieMiddleware)
# This limit must run before FastAPI parses multipart bodies; keep CORS outermost
# so browser clients also receive CORS headers on a 413 response.
app.add_middleware(UploadRequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    environment: str
    postgres: str
    redis: str
    qdrant: str
    slither: str


def _check_redis() -> bool:
    client = redis.from_url(settings.redis_url, decode_responses=True)
    return client.ping()


def _check_qdrant() -> bool:
    response = httpx.get(
        f"{settings.qdrant_url.rstrip('/')}/healthz",
        timeout=5.0,
        trust_env=False,
    )
    return response.status_code == 200


def _check_slither() -> bool:
    response = httpx.get(
        f"{settings.slither_url.rstrip('/')}/health",
        timeout=5.0,
        trust_env=False,
    )
    return response.status_code == 200


@app.get("/")
def root() -> dict:
    return {"service": "chainwise-backend", "docs": "/docs"}


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    checks = {"postgres": "error", "redis": "error", "qdrant": "error", "slither": "error"}

    try:
        check_postgres()
        checks["postgres"] = "ok"
    except Exception as exc:
        logger.warning("Postgres check failed: %s", exc)

    try:
        if _check_redis():
            checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("Redis check failed: %s", exc)

    try:
        if _check_qdrant():
            checks["qdrant"] = "ok"
    except Exception as exc:
        logger.warning("Qdrant check failed: %s", exc)

    try:
        if _check_slither():
            checks["slither"] = "ok"
    except Exception as exc:
        logger.warning("Slither check failed: %s", exc)

    all_ok = all(v == "ok" for v in checks.values())
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        environment=settings.environment,
        **checks,
    )
