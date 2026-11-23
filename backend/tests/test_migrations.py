"""資料庫結構檔：初始結構檔要凍結成固定定義，且遷移後的結構要與模型一致（含 CHECK）。"""
import re
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, create_engine, text

from app.models import Base
from tests.conftest import TEST_ADMIN_DB

BACKEND = Path(__file__).resolve().parents[1]
_LITERAL = re.compile(r"'([^']*)'")


def test_initial_migration_is_frozen():
    src = (BACKEND / "migrations/versions/0001_init.py").read_text(encoding="utf-8")
    assert "create_all" not in src and "app.models" not in src


def _model_checks() -> dict[str, set[str]]:
    out = {}
    for table in Base.metadata.sorted_tables:
        for c in table.constraints:
            if isinstance(c, CheckConstraint):
                out[c.name] = set(_LITERAL.findall(str(c.sqltext)))
    return out


def test_migrations_match_models(migrated_db):
    eng = create_engine(TEST_ADMIN_DB)
    try:
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            rows = conn.execute(text(
                "select conname, pg_get_constraintdef(c.oid) from pg_constraint c "
                "join pg_namespace n on n.oid = c.connamespace "
                "where c.contype = 'c' and n.nspname = 'public'"
            )).all()
    finally:
        eng.dispose()
    assert diff == []
    db_checks = {name: set(_LITERAL.findall(d)) for name, d in rows}
    assert db_checks == _model_checks()
