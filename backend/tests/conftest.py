from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import Settings

BACKEND = Path(__file__).resolve().parent.parent
TEST_DB = "postgresql+psycopg://app_user:apppass@127.0.0.1:55432/legaltech_test"
TEST_ADMIN_DB = "postgresql+psycopg://postgres:devpass@127.0.0.1:55432/legaltech_test"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        app_env="dev",
        database_url=TEST_DB,
        database_admin_url=TEST_ADMIN_DB,
        storage_dir=tmp_path / "storage",
        session_secret="test-secret",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def migrated_db():
    """整個測試階段只建一次：清空 public schema 後跑 alembic upgrade head。"""
    from alembic import command
    from alembic.config import Config

    eng = create_engine(TEST_ADMIN_DB, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_ADMIN_DB)
    command.upgrade(cfg, "head")
    return True


@pytest.fixture
def db(migrated_db, test_settings):
    """每個測試前清空所有資料表，並把 app.db 指向測試資料庫。"""
    from app import db as dbmod
    from app.models import Base

    dbmod.configure(test_settings)
    eng = create_engine(TEST_ADMIN_DB)
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with eng.begin() as c:
        c.execute(text(f"TRUNCATE {names} CASCADE"))
    eng.dispose()
    yield test_settings
