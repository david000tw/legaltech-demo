"""狀頭與當事人自動帶入、逐欄核對（設計 6.6）：只填空的欄位、只補對造；有未核對的自動帶入欄位就不能匯出。"""
import pytest
from sqlalchemy import select

from app.db import tenant_session
from app.header_extract import apply_header_guess, header_unverified
from app.ingest.service import ingest_document
from app.models import Case, CaseParty, Document, Round
from app.verify.citations import export_blockers
from tests.export_helpers import clients
from tests.test_header_extract import COMPLAINT, PREPARATORY
from tests.verify_helpers import setup_verify_round


def _case(firm, case_id, **fields):
    with tenant_session(firm) as s:
        case = s.get(Case, case_id)
        for k, v in fields.items():
            setattr(case, k, v)


def _brief(firm, case_id, round_id, text, name="書狀.txt"):
    with tenant_session(firm) as s:
        return ingest_document(s, firm, case_id, "opponent_brief", name, text.encode("utf-8"), round_id).id


def _apply(firm, case_id, doc_id):
    with tenant_session(firm) as s:
        return apply_header_guess(s, case_id, doc_id)


@pytest.fixture
def ctx(db):
    c = setup_verify_round(our_side="defendant")
    with tenant_session(c["firm"]) as s:
        s.add(CaseParty(firm_id=c["firm"], case_id=c["case_id"], seq=1, name="王小華", role="被告", alignment="ours"))
    c["clients"] = clients(c, db)
    return c


def test_apply_fills_only_empty_fields(ctx):
    _case(ctx["firm"], ctx["case_id"], court="臺灣臺北地方法院")
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY)
    _apply(ctx["firm"], ctx["case_id"], doc)
    with tenant_session(ctx["firm"]) as s:
        case = s.get(Case, ctx["case_id"])
        assert case.court == "臺灣臺北地方法院" and "court" not in case.header_auto
        assert case.case_number == "114年度訴字第123號"
        assert case.header_auto["case_number"]["document_id"] == str(doc)
        assert case.header_auto["case_number"]["verified_by"] is None


def test_apply_adds_only_opposing_parties(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], COMPLAINT)
    _apply(ctx["firm"], ctx["case_id"], doc)
    with tenant_session(ctx["firm"]) as s:
        parties = {p.name: p for p in s.scalars(select(CaseParty).where(CaseParty.case_id == ctx["case_id"]))}
    assert parties["林大明"].alignment == "opposing" and parties["林大明"].role == "原告"
    assert parties["林大明"].auto_document_id == doc and parties["林大明"].auto_verified is False
    assert "陳美玲" not in parties  # 共同被告和我方同一邊：歸屬分不出來，不自動加


def test_apply_sets_filer_when_single_opponent(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], COMPLAINT)
    _apply(ctx["firm"], ctx["case_id"], doc)
    with tenant_session(ctx["firm"]) as s:
        filer = s.get(CaseParty, s.get(Document, doc).filed_by_party_id)
        assert filer.name == "林大明"


def test_apply_is_idempotent(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], COMPLAINT)
    _apply(ctx["firm"], ctx["case_id"], doc)
    assert _apply(ctx["firm"], ctx["case_id"], doc) == []


def _blocked(ctx):
    with tenant_session(ctx["firm"]) as s:
        return any(b.reason == "header_unverified" and b.scope == "draft" for b in export_blockers(s, ctx["draft_id"]))


def test_export_blocked_until_header_verified(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY)
    _apply(ctx["firm"], ctx["case_id"], doc)
    assert _blocked(ctx)
    assert ctx["clients"]["assistant"].post(f"/api/cases/{ctx['case_id']}/header-verify").status_code == 403
    r = ctx["clients"]["lawyer"].post(f"/api/cases/{ctx['case_id']}/header-verify")
    assert r.status_code == 200, r.text
    assert not _blocked(ctx)
    with tenant_session(ctx["firm"]) as s:
        entry = s.get(Case, ctx["case_id"]).header_auto["case_number"]
        assert entry["verified_bar"] and entry["verified_by"]


def test_unrelated_patch_does_not_verify(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY)
    _apply(ctx["firm"], ctx["case_id"], doc)
    lawyer = ctx["clients"]["lawyer"]
    header = lawyer.get(f"/api/cases/{ctx['case_id']}").json()["header"]
    assert lawyer.patch(f"/api/cases/{ctx['case_id']}", json={**header, "addressee": "民事庭"}).status_code == 200
    with tenant_session(ctx["firm"]) as s:
        assert header_unverified(s, ctx["case_id"])
    assert lawyer.patch(f"/api/cases/{ctx['case_id']}", json={"case_number": "114年度訴字第124號"}).status_code == 200
    with tenant_session(ctx["firm"]) as s:  # 律師改過的欄位視為已核對（其他自動帶入欄位仍未核對）
        auto = s.get(Case, ctx["case_id"]).header_auto
        assert auto["case_number"]["verified_by"] and auto["division"]["verified_by"] is None


def test_assistant_edit_does_not_verify(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY)
    _apply(ctx["firm"], ctx["case_id"], doc)
    assistant = ctx["clients"]["assistant"]
    assert assistant.patch(f"/api/cases/{ctx['case_id']}", json={"case_number": "114年度訴字第125號"}).status_code == 200
    with tenant_session(ctx["firm"]) as s:
        assert s.get(Case, ctx["case_id"]).header_auto["case_number"]["verified_by"] is None


def test_new_brief_fills_blank_field_needs_reverify(ctx):
    first = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], COMPLAINT)  # 起訴狀：還沒有案號
    _apply(ctx["firm"], ctx["case_id"], first)
    assert ctx["clients"]["lawyer"].post(f"/api/cases/{ctx['case_id']}/header-verify").status_code == 200
    assert not _blocked(ctx)
    later = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY, name="準備書狀.txt")
    assert "case_number" in _apply(ctx["firm"], ctx["case_id"], later)
    assert _blocked(ctx)


def test_case_view_shows_auto_filled_sources(ctx):
    doc = _brief(ctx["firm"], ctx["case_id"], ctx["round_id"], PREPARATORY)
    _apply(ctx["firm"], ctx["case_id"], doc)
    body = ctx["clients"]["lawyer"].get(f"/api/cases/{ctx['case_id']}").json()
    assert body["header_auto"]["case_number"] == {"filename": "書狀.txt", "page_no": 1, "verified": False,
                                                  "document_id": str(doc)}
    assert body["header_unverified"] is True
    opp = next(p for p in body["parties"] if p["name"] == "林大明")
    assert opp["auto"] is True and opp["auto_verified"] is False
