"""子專案丙 Task 4：試算 API、案件帶入值、繕本份數、行事曆匯入。"""
from pathlib import Path

from sqlalchemy import func, select

from app.db import admin_session, tenant_session
from app.models import Case, CaseParty, Round, WorkCalendarDay
from app.tools import DISCLAIMER
from app.tools.calendar_loader import load_calendar
from app.tools.copies import count
from tests.pipeline_helpers import make_round

CALENDAR = Path(__file__).resolve().parent.parent / "seed" / "calendar"


def _party(seq, name, role, alignment):
    return CaseParty(seq=seq, name=name, role=role, alignment=alignment)


def test_copies_aligned_defendant_counts_as_other_side():
    n, steps = count([_party(1, "甲", "原告", "ours"), _party(2, "乙", "被告", "opposing"),
                      _party(3, "丙", "被告", "aligned")])
    assert n == 4
    assert all(s.basis for s in steps) and any("民事訴訟法第119條" in s.basis for s in steps)
    n, steps = count([_party(1, "乙", "被告", "opposing")])
    assert n is None and any("請先在當事人標示我方" in (s.value + s.basis) for s in steps)


def test_tools_api_always_has_disclaimer(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    fee = client.post("/api/tools/court-fee", json={"property": True, "amount": 405_000, "instance": 1}).json()
    deadline = client.post("/api/tools/deadline", json={
        "kind": "appeal", "procedure": "civil", "travel_days": 0, "attorney_local": False,
        "services": [{"who": "party", "served_on": "2026-03-01", "method": "deposit"}]})
    assert deadline.status_code == 200, deadline.text
    deadline = deadline.json()
    defaults = client.get(f"/api/cases/{case_id}/tool-defaults").json()
    for body, steps in ((fee, fee["steps"]), (deadline, deadline["steps"]), (defaults, defaults["copies_steps"])):
        assert DISCLAIMER in body["notes"]
        assert steps and all(s["basis"] for s in steps)
    assert deadline["due"] == "2026-03-31"


def test_deadline_api_rejects_bad_combo(lawyer_client):
    client, _, _ = lawyer_client
    r = client.post("/api/tools/deadline", json={
        "kind": "appeal", "procedure": "family_nonlitigation", "travel_days": 0, "attorney_local": False,
        "services": [{"who": "party", "served_on": "2026-03-01", "method": "direct"}]})
    assert r.status_code == 422
    assert r.json()["detail"] == "家事訴訟事件請選上訴；家事非訟事件請選抗告；或改用自訂天數"


def test_deadline_api_uses_work_calendar(lawyer_client):
    client, _, _ = lawyer_client
    with admin_session() as s:
        load_calendar(s, CALENDAR)
    # 2026-03-11 寄存 → 3/21 生效 → 4/10 屆滿（星期五，上班日）
    r = client.post("/api/tools/deadline", json={
        "kind": "appeal", "procedure": "civil", "travel_days": 0, "attorney_local": False,
        "services": [{"who": "party", "served_on": "2026-03-11", "method": "deposit"}]}).json()
    assert r["due"] == "2026-04-10"
    assert "行事曆資料未涵蓋，只順延週末" not in r["notes"]


def test_deadline_api_long_period_uses_whole_calendar(lawyer_client):
    """後端階段審查 #2：自訂 365 日＋在途 50 日，屆滿日落在春節連假，要讀得到那段行事曆。"""
    client, _, _ = lawyer_client
    with admin_session() as s:
        load_calendar(s, CALENDAR)
    r = client.post("/api/tools/deadline", json={
        "kind": "custom", "procedure": "civil", "custom_days": 365, "travel_days": 50, "attorney_local": False,
        "services": [{"who": "party", "served_on": "2025-01-01", "method": "direct"}]}).json()
    assert r["due"] == "2026-02-23"
    assert "行事曆資料未涵蓋，只順延週末" not in r["notes"]


def test_tool_defaults_from_case(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, round_id = make_round(firm)
    with tenant_session(firm) as s:
        case = s.get(Case, case_id)
        case.case_number = "114年度家繼訴字第15號"
        s.get(Round, round_id).brief_kind = "complaint"
        s.get(Round, round_id).claim_value = 405_000
        s.add_all([CaseParty(firm_id=firm, case_id=case_id, seq=1, name="甲", role="原告", alignment="ours"),
                   CaseParty(firm_id=firm, case_id=case_id, seq=2, name="乙", role="被告", alignment="opposing")])
    body = client.get(f"/api/cases/{case_id}/tool-defaults").json()
    assert body["amount"] == 405_000
    assert body["deadline_procedure"] == "family_litigation"
    assert body["copies"] == 3


def test_tool_defaults_civil_and_other_firm(lawyer_client):
    from tests.helpers import create_firm
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    body = client.get(f"/api/cases/{case_id}/tool-defaults").json()
    assert body["deadline_procedure"] == "civil" and body["copies"] is None
    other_case, _ = make_round(create_firm("乙事務所"))
    assert client.get(f"/api/cases/{other_case}/tool-defaults").status_code == 404


def test_calendar_loader_idempotent(db):
    with admin_session() as s:
        first = load_calendar(s, CALENDAR)
    with admin_session() as s:
        second = load_calendar(s, CALENDAR)
        total = s.scalar(select(func.count()).select_from(WorkCalendarDay))
        makeup = s.get(WorkCalendarDay, __import__("datetime").date(2025, 2, 8))
        new_year = s.get(WorkCalendarDay, __import__("datetime").date(2026, 1, 1))
    assert first == second == total == 365 * 3
    assert makeup.is_holiday is False and "補行上班" in makeup.note
    assert new_year.is_holiday is True
