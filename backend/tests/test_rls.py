import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import db as dbmod
from app.db import admin_session, app_session, tenant_session
from app.models import PUBLIC_TABLES, Case, Firm, Member, Round
from tests.conftest import TEST_DB


def make_firm_with_case(name: str):
    with admin_session() as s:
        f = Firm(name=name)
        s.add(f)
        s.flush()
        s.add(Case(firm_id=f.id, title=f"{name} 的案件", our_side="plaintiff"))
        return f.id


def test_cross_firm_select_returns_nothing(db):
    a = make_firm_with_case("A")
    make_firm_with_case("B")
    with tenant_session(a) as s:
        cases = s.scalars(select(Case)).all()
    assert len(cases) == 1
    assert cases[0].firm_id == a


def test_cross_firm_insert_rejected(db):
    a = make_firm_with_case("A")
    b = make_firm_with_case("B")
    with pytest.raises(DBAPIError, match="row-level security"):
        with tenant_session(a) as s:
            s.add(Case(firm_id=b, title="偷塞", our_side="plaintiff"))
            s.flush()


def test_cross_firm_foreign_key_rejected(db):
    """外鍵檢查會繞過 RLS，所以子表必須以 (firm_id, 父表 id) 複合外鍵指向父表。"""
    a = make_firm_with_case("A")
    b = make_firm_with_case("B")
    with admin_session() as s:
        b_case = s.scalars(select(Case).where(Case.firm_id == b)).one().id
    with pytest.raises(IntegrityError):
        with tenant_session(a) as s:
            s.add(Round(firm_id=a, case_id=b_case, seq=1, status="created"))
            s.flush()


def test_app_role_has_no_bypassrls(db):
    with admin_session() as s:
        v = s.execute(text("select rolbypassrls from pg_roles where rolname='app_user'")).scalar_one()
    assert v is False


def test_every_non_public_table_has_enabled_forced_rls_and_policy(db):
    """查資料庫實際的資料表，不依賴 models 推算：公開表以外都要 ENABLE＋FORCE＋tenant_isolation。"""
    with admin_session() as s:
        rows = s.execute(text(
            "select c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "  exists(select 1 from pg_policies p where p.schemaname='public' and p.tablename=c.relname "
            "         and p.policyname='tenant_isolation') "
            "from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname='public' and c.relkind='r'"
        )).all()
    tables = {r[0]: r[1:] for r in rows}
    business = set(tables) - set(PUBLIC_TABLES) - {"alembic_version"}
    assert {"cases", "firms", "members", "citations"} <= business
    bad = {t: tables[t] for t in business if tables[t] != (True, True, True)}
    assert bad == {}


def test_public_tables_are_read_only_for_app_user(db):
    with admin_session() as s:
        for t in PUBLIC_TABLES:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                has = s.execute(text("select has_table_privilege('app_user', :t, :p)"),
                                {"t": f"public.{t}", "p": priv}).scalar_one()
                assert has is (priv == "SELECT"), (t, priv)


def test_firms_and_members_not_writable_by_app_user(db):
    """is_internal_test、bar_verified、role 是合規門檻，一般請求用的帳號不得修改。"""
    with admin_session() as s:
        for t in ("firms", "members"):
            for priv in ("INSERT", "UPDATE", "DELETE"):
                assert s.execute(text("select has_table_privilege('app_user', :t, :p)"),
                                 {"t": f"public.{t}", "p": priv}).scalar_one() is False, (t, priv)
    a = make_firm_with_case("A")
    with pytest.raises(DBAPIError, match="permission denied"):
        with tenant_session(a) as s:
            s.execute(text("update firms set is_internal_test = true"))


def test_email_unique_case_insensitive(db):
    with admin_session() as s:
        f = Firm(name="A")
        s.add(f)
        s.flush()
        s.add(Member(firm_id=f.id, email="X@p.test", password_hash="h", display_name="x", role="lawyer"))
    with pytest.raises(IntegrityError):
        with admin_session() as s:
            f2 = Firm(name="B")
            s.add(f2)
            s.flush()
            s.add(Member(firm_id=f2.id, email="x@p.test", password_hash="h", display_name="x", role="lawyer"))


def test_no_firm_context_sees_nothing(db):
    make_firm_with_case("A")
    with app_session() as s:
        assert s.scalars(select(Case)).all() == []


def test_context_survives_commit(db):
    a = make_firm_with_case("A")
    make_firm_with_case("B")
    with tenant_session(a) as s:
        s.add(Case(firm_id=a, title="第二件", our_side="defendant"))
        s.commit()
        titles = sorted(c.title for c in s.scalars(select(Case)))
    assert titles == ["A 的案件", "第二件"]


def test_reused_connection_after_tenant_tx_sees_nothing(db, monkeypatch):
    single = create_engine(TEST_DB, pool_size=1, max_overflow=0)
    monkeypatch.setitem(dbmod._engines, TEST_DB, single)
    a = make_firm_with_case("A")
    with tenant_session(a) as s:
        assert len(s.scalars(select(Case)).all()) == 1
        s.commit()
    with app_session() as s:
        assert s.scalars(select(Case)).all() == []
    single.dispose()


def test_auth_lookup_returns_only_auth_fields(db):
    with admin_session() as s:
        f = Firm(name="A")
        s.add(f)
        s.flush()
        s.add(Member(firm_id=f.id, email="lawyer@example.test", password_hash="h",
                     display_name="王律師", role="lawyer"))
    with app_session() as s:
        res = s.execute(text("select * from auth_lookup(:e)"), {"e": "lawyer@example.test"})
        assert list(res.keys()) == ["member_id", "firm_id", "password_hash"]
        assert len(res.all()) == 1
        assert s.execute(text("select * from members")).all() == []
