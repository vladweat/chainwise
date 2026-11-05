"""Route pytest at `chainwise_test` so fixtures never wipe the dev database.

Must run before any test module imports `app.core.database` / `app.main`.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

TEST_DB_NAME = "chainwise_test"

_ORIGINAL_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://chainwise:change_this_in_prod@postgres:5432/chainwise_db",
)


def _with_db_name(url: str, name: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{name}"))


def _database_name(url: str) -> str:
    return urlparse(url).path.lstrip("/").split("?")[0]


os.environ["DATABASE_URL"] = _with_db_name(_ORIGINAL_DATABASE_URL, TEST_DB_NAME)
os.environ["COOKIE_SAMESITE"] = "lax"
os.environ["COOKIE_SECURE"] = "false"


def _ensure_test_database() -> None:
    from sqlalchemy import create_engine, text

    admin_engine = create_engine(_ORIGINAL_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin_engine.dispose()


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    from app.core.config import get_settings

    get_settings.cache_clear()
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


_ensure_test_database()
_run_migrations()


def pytest_sessionstart() -> None:
    db_name = _database_name(os.environ.get("DATABASE_URL", ""))
    if db_name != TEST_DB_NAME:
        raise RuntimeError(
            f"Refusing to run tests against database {db_name!r}; expected {TEST_DB_NAME!r}."
        )
