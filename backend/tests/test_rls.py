import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError

from app import db as dbmod
from app.db import admin_session, app_session, tenant_session
from app.models import Base, Case, Firm, Member
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
    with pytest.raises(DBAPIError):
        with tenant_session(a) as s:
            s.add(Case(firm_id=b, title="偷塞", our_side="plaintiff"))
            s.flush()


def test_app_role_has_no_bypassrls(db):
    with admin_session() as s:
        v = s.execute(text("select rolbypassrls from pg_roles where rolname='app_user'")).scalar_one()
    assert v is False


def test_all_business_tables_force_rls(db):
    business = [t.name for t in Base.metadata.sorted_tables if "firm_id" in t.c or t.name == "firms"]
    assert "cases" in business and "firms" in business
    with admin_session() as s:
        rows = dict(s.execute(text(
            "select relname, relforcerowsecurity from pg_class where relname = any(:n)"
        ), {"n": business}).all())
    assert set(rows) == set(business)
    assert all(rows.values()), rows


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
