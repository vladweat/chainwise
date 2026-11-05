from app.core.config import get_settings


def test_pytest_uses_isolated_database():
    assert get_settings().database_url.rstrip("/").endswith("chainwise_test")
