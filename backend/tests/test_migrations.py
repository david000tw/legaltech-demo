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


def test_0004_fills_attorneys_of_existing_cases(db):
    """critic L2：計畫 1 的舊案件升級後，訴訟代理人帶入建立案件的律師（助理建立的維持空白）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_ADMIN_DB)
    eng = create_engine(TEST_ADMIN_DB, isolation_level="AUTOCOMMIT")
    command.downgrade(cfg, "0003")
    try:
        with eng.connect() as c:
            firm = c.execute(text("INSERT INTO firms (id, name, is_internal_test) VALUES (gen_random_uuid(), '舊所', true) "
                                  "RETURNING id")).scalar()
            ids = {}
            for who, role, name in (("a", "lawyer", "王大明"), ("b", "lawyer", "李小華律師"), ("c", "assistant", "助理甲")):
                ids[who] = c.execute(text(
                    "INSERT INTO members (id, firm_id, email, password_hash, display_name, role, bar_verified) "
                    "VALUES (gen_random_uuid(), :f, :e, 'x', :n, :r, false) RETURNING id"),
                    {"f": firm, "e": f"{who}@old.test", "n": name, "r": role}).scalar()
                c.execute(text("INSERT INTO cases (id, firm_id, title, our_side, case_type, created_by) "
                               "VALUES (gen_random_uuid(), :f, :t, 'plaintiff', 'traffic_tort', :m)"),
                          {"f": firm, "t": f"舊案{who}", "m": ids[who]})
            c.execute(text("INSERT INTO cases (id, firm_id, title, our_side, case_type) "
                           "VALUES (gen_random_uuid(), :f, '舊案無建立者', 'plaintiff', 'traffic_tort')"), {"f": firm})
    finally:
        command.upgrade(cfg, "head")
    with eng.connect() as c:
        got = dict(c.execute(text("SELECT title, attorneys FROM cases WHERE title LIKE '舊案%'")).all())
    eng.dispose()
    assert got == {"舊案a": ["王大明律師"], "舊案b": ["李小華律師"], "舊案c": [], "舊案無建立者": []}


def _cfg():
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_ADMIN_DB)
    return cfg


def test_0007_round_trip(db):
    """最終審查：律師刪掉的 AI 事件改成隱藏（origin='dismissed'），重新整理才不會找回來。"""
    from alembic import command
    cfg = _cfg()

    def origin_check():
        eng = create_engine(TEST_ADMIN_DB)
        try:
            with eng.connect() as c:
                return c.execute(text("select pg_get_constraintdef(oid) from pg_constraint "
                                      "where conname = 'ck_timeline_origin'")).scalar_one()
        finally:
            eng.dispose()

    command.downgrade(cfg, "0006")
    try:
        assert "dismissed" not in origin_check()
    finally:
        command.upgrade(cfg, "head")
    assert "dismissed" in origin_check()


def test_0006_round_trip(db):
    """子專案乙：AI 工作、卷證說明、大事記、健檢四張表可以降級再升級（RLS 由 test_rls 的通用檢查涵蓋）。"""
    from alembic import command
    cfg = _cfg()
    new = {"ai_jobs", "evidence_notes", "timeline_events", "health_reports"}

    def schema():
        eng = create_engine(TEST_ADMIN_DB)
        try:
            with eng.connect() as c:
                tables = set(c.execute(text("select tablename from pg_tables where schemaname='public'")).scalars())
                cols = set(c.execute(text("select column_name from information_schema.columns "
                                          "where table_name='documents'")).scalars())
                return tables, cols
        finally:
            eng.dispose()

    command.downgrade(cfg, "0005")
    try:
        tables, cols = schema()
        assert not new & tables and "notes_fingerprint" not in cols
    finally:
        command.upgrade(cfg, "head")
    tables, cols = schema()
    assert new <= tables and "notes_fingerprint" in cols


def test_0005_keeps_old_rounds_usable(db):
    """子專案甲：升級後舊輪次照常可用；執行中、失敗的輪次都能重新拆解；降級可回復（新狀態對回舊狀態）。"""
    from alembic import command
    cfg = _cfg()
    eng = create_engine(TEST_ADMIN_DB, isolation_level="AUTOCOMMIT")
    command.downgrade(cfg, "0004")
    try:
        with eng.connect() as c:
            firm = c.execute(text("INSERT INTO firms (id, name, is_internal_test) VALUES (gen_random_uuid(), '舊所', true) "
                                  "RETURNING id")).scalar()
            case = c.execute(text("INSERT INTO cases (id, firm_id, title, our_side, case_type) "
                                  "VALUES (gen_random_uuid(), :f, '舊案', 'plaintiff', 'traffic_tort') RETURNING id"),
                             {"f": firm}).scalar()
            for seq, status, error in ((1, "drafted", None), (2, "running", None), (3, "failed", "usage_limit: x")):
                c.execute(text("INSERT INTO rounds (id, firm_id, case_id, seq, status, error) "
                               "VALUES (gen_random_uuid(), :f, :c, :s, :st, :e)"),
                          {"f": firm, "c": case, "s": seq, "st": status, "e": error})
    finally:
        command.upgrade(cfg, "head")
    with eng.connect() as c:
        rows = {r[0]: tuple(r[1:]) for r in c.execute(text(
            "SELECT seq, status, failed_stage, error, brief_kind FROM rounds WHERE case_id = :c"), {"c": case})}
    assert rows[1] == ("drafted", None, None, "preparatory")
    assert rows[2] == ("failed", "extract", "系統升級時中斷，請重新執行", "preparatory")
    assert rows[3] == ("failed", "extract", "usage_limit: x", "preparatory")

    with eng.connect() as c:
        for seq, status in ((4, "extracted"), (5, "drafting"), (6, "extracting")):
            c.execute(text("INSERT INTO rounds (id, firm_id, case_id, seq, status) VALUES (gen_random_uuid(), :f, :c, :s, :st)"),
                      {"f": firm, "c": case, "s": seq, "st": status})
        r1 = c.execute(text("SELECT id FROM rounds WHERE case_id = :c AND seq = 1"), {"c": case}).scalar()
        draft = c.execute(text("INSERT INTO drafts (id, firm_id, round_id) VALUES (gen_random_uuid(), :f, :r) RETURNING id"),
                          {"f": firm, "r": r1}).scalar()
        para = c.execute(text("INSERT INTO paragraphs (id, firm_id, draft_id, seq, heading, text, status, verify_reasons) "
                              "VALUES (gen_random_uuid(), :f, :d, 1, '', '內文', 'unreviewed', '[]'::jsonb) RETURNING id"),
                         {"f": firm, "d": draft}).scalar()
        c.execute(text("INSERT INTO review_events (id, firm_id, paragraph_id, action, after_text) "
                       "VALUES (gen_random_uuid(), :f, :p, 'confirm_amount', '406000')"), {"f": firm, "p": para})
    command.downgrade(cfg, "0004")
    try:
        with eng.connect() as c:
            got = dict(c.execute(text("SELECT seq, status FROM rounds WHERE case_id = :c"), {"c": case}).all())
            events = c.execute(text("SELECT count(*) FROM review_events")).scalar()
    finally:
        command.upgrade(cfg, "head")
        eng.dispose()
    assert got[4] == "created" and got[5] == "failed" and got[6] == "failed"
    assert events == 0
