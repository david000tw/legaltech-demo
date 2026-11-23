import pytest

from app.config import Settings

TEST_DB = "postgresql+psycopg://app_user:apppass@127.0.0.1:55432/legaltech_test"
TEST_ADMIN_DB = "postgresql+psycopg://postgres:devpass@127.0.0.1:55432/legaltech_test"


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="dev",
        database_url=TEST_DB,
        database_admin_url=TEST_ADMIN_DB,
        storage_dir=tmp_path / "storage",
        session_secret="test-secret",
    )
