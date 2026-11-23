import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.config import Settings

BACKEND = Path(__file__).resolve().parent.parent
# 平行執行（例如審查者同時跑測試）時，以 LEGALTECH_TEST_DB 指定不同的測試資料庫；不存在會自動建立。
TEST_DB_NAME = os.environ.get("LEGALTECH_TEST_DB", "legaltech_test")
TEST_DB = f"postgresql+psycopg://app_user:apppass@127.0.0.1:55432/{TEST_DB_NAME}"
TEST_ADMIN_DB = f"postgresql+psycopg://postgres:devpass@127.0.0.1:55432/{TEST_DB_NAME}"
SERVER_ADMIN_DB = "postgresql+psycopg://postgres:devpass@127.0.0.1:55432/postgres"


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

    server = create_engine(SERVER_ADMIN_DB, isolation_level="AUTOCOMMIT")
    with server.connect() as c:
        if not c.execute(text("select 1 from pg_database where datname = :n"), {"n": TEST_DB_NAME}).first():
            c.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    server.dispose()
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


@pytest.fixture
def lawyer_client(db):
    """已登入的已驗證律師（事務所「甲事務所」，內部測試）。回傳 (client, firm_id, member_id)。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.helpers import create_firm, create_member, login

    firm_id = create_firm("甲事務所")
    member_id = create_member(firm_id, "lawyer@example.test")
    client = TestClient(create_app(db))
    assert login(client, "lawyer@example.test").status_code == 200
    return client, firm_id, member_id
