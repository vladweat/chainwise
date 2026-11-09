import pytest

from app.core.config import Settings

VALID_SECRET = "f2179c4a0e63b58d761f2084a3ce9d65f2179c4a0e63b58d761f2084a3ce9d65"
VALID_DB_PASSWORD = "a9d3e70c5b2f8146a9d3e70c5b2f8146a9d3e70c5b2f8146a9d3e70c5b2f8146"
WEAK_SEQUENTIAL_VALUE = "abcdefghijklmnopqrstuvwxyz012345"


def _production_settings(monkeypatch, **overrides) -> Settings:
    values = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": f"postgresql://chainwise:{VALID_DB_PASSWORD}@postgres:5432/chainwise_db",
        "SECRET_KEY": VALID_SECRET,
        "ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "COOKIE_SECURE": "true",
        "COOKIE_SAMESITE": "lax",
        "CORS_ORIGINS": "https://chainwise.example.com",
        "NEXT_PUBLIC_API_URL": "https://chainwise.example.com",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))
    return Settings(_env_file=None)


def test_accepts_valid_production_settings(monkeypatch):
    settings = _production_settings(monkeypatch)

    assert settings.environment == "production"
    assert settings.secret_key == VALID_SECRET
    assert settings.access_token_expire_minutes == 15
    assert settings.refresh_token_expire_minutes == 10080
    assert settings.cookie_secure is True
    assert settings.cors_origin_list() == ["https://chainwise.example.com"]


@pytest.mark.parametrize("minutes", [5, 60])
def test_accepts_production_access_token_policy_boundaries(monkeypatch, minutes):
    settings = _production_settings(monkeypatch, ACCESS_TOKEN_EXPIRE_MINUTES=str(minutes))

    assert settings.access_token_expire_minutes == minutes


@pytest.mark.parametrize(
    ("overrides", "unsafe_value", "field"),
    [
        ({"SECRET_KEY": "change_this_secret_key_in_prod"}, "change_this_secret_key_in_prod", "SECRET_KEY"),
        ({"SECRET_KEY": "a" * 64}, "a" * 64, "SECRET_KEY"),
        ({"SECRET_KEY": WEAK_SEQUENTIAL_VALUE}, WEAK_SEQUENTIAL_VALUE, "SECRET_KEY"),
        (
            {"DATABASE_URL": "postgresql://chainwise:change_this_in_prod@postgres:5432/chainwise_db"},
            "change_this_in_prod",
            "DATABASE_URL",
        ),
        (
            {"DATABASE_URL": f"postgresql://chainwise:{'a' * 64}@postgres:5432/chainwise_db"},
            "a" * 64,
            "DATABASE_URL",
        ),
        (
            {"DATABASE_URL": f"postgresql://chainwise:{WEAK_SEQUENTIAL_VALUE}@postgres:5432/chainwise_db"},
            WEAK_SEQUENTIAL_VALUE,
            "DATABASE_URL",
        ),
        ({"DATABASE_URL": "not-a-postgresql-url-secret-value"}, "not-a-postgresql-url-secret-value", "DATABASE_URL"),
        ({"COOKIE_SECURE": "false"}, "false", "COOKIE_SECURE"),
        ({"CORS_ORIGINS": "http://localhost:3001"}, "http://localhost:3001", "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "https://localhost:3001"}, "https://localhost:3001", "CORS_ORIGINS"),
        ({"CORS_ORIGINS": "http://chainwise.example.com"}, "http://chainwise.example.com", "CORS_ORIGINS"),
        (
            {"CORS_ORIGINS": "https://chainwise.example.com,https://localhost:3001"},
            "https://localhost:3001",
            "CORS_ORIGINS",
        ),
        ({"NEXT_PUBLIC_API_URL": "http://localhost:8001"}, "http://localhost:8001", "NEXT_PUBLIC_API_URL"),
        ({"NEXT_PUBLIC_API_URL": "https://localhost:8001"}, "https://localhost:8001", "NEXT_PUBLIC_API_URL"),
        ({"ACCESS_TOKEN_EXPIRE_MINUTES": "1440"}, "1440", "ACCESS_TOKEN_EXPIRE_MINUTES"),
        ({"ACCESS_TOKEN_EXPIRE_MINUTES": "4"}, "4", "ACCESS_TOKEN_EXPIRE_MINUTES"),
        ({"COOKIE_SAMESITE": "invalid"}, "invalid", "COOKIE_SAMESITE"),
    ],
)
def test_rejects_unsafe_production_settings_without_echoing_values(monkeypatch, overrides, unsafe_value, field):
    with pytest.raises(RuntimeError) as error:
        _production_settings(monkeypatch, **overrides)

    message = str(error.value)
    assert field in message
    assert unsafe_value not in message


def test_development_defaults_remain_unchanged(monkeypatch):
    for name in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "SECRET_KEY",
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "COOKIE_SECURE",
        "COOKIE_SAMESITE",
        "CORS_ORIGINS",
        "NEXT_PUBLIC_API_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.database_url == "postgresql+psycopg2://chainwise:change_this_in_prod@localhost:5432/chainwise_db"
    assert settings.secret_key == "change_this_secret_key_in_prod"
    assert settings.access_token_expire_minutes == 1440
    assert settings.refresh_token_expire_minutes == 10080
    assert settings.cookie_secure is False
    assert settings.cookie_samesite == "lax"
    assert settings.cors_origin_list() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]
    assert settings.next_public_api_url == "http://localhost:8001"


def test_production_launch_rejects_uvicorn_reload(monkeypatch):
    import app.core.config as config

    settings = _production_settings(monkeypatch)
    monkeypatch.setattr(config.sys, "argv", ["uvicorn", "app.main:app", "--reload"])

    with pytest.raises(RuntimeError, match="--reload"):
        config.validate_production_launch(settings)


def test_development_launch_keeps_uvicorn_reload_available(monkeypatch):
    from app.core.config import validate_production_launch

    settings = _production_settings(monkeypatch, ENVIRONMENT="development")

    validate_production_launch(settings, ["uvicorn", "app.main:app", "--reload"])
