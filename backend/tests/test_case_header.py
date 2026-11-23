"""案件狀頭欄位、訴訟代理人、當事人清單與遞狀人（設計 5.2）。"""
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import import_testcase
from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Case, CaseParty, Document
from app.pipeline.case_context import case_context
from tests.helpers import create_firm, create_member, login

CASES = Path(__file__).resolve().parent.parent / "testdata" / "cases"
PARTIES = [
    {"name": "宋文彬", "role": "原告", "alignment": "ours"},
    {"name": "宋文德", "role": "被告", "alignment": "opposing"},
    {"name": "宋文琪", "role": "被告", "alignment": "aligned", "position_note": "同意分割，但爭執遺產範圍"},
]


def _estate_case(client) -> str:
    r = client.post("/api/cases", json={
        "title": "宋文彬 分割遺產", "our_side": "plaintiff", "case_type": "estate_partition",
        "court": "臺灣臺北地方法院", "case_number": "113年度家繼訴字第89號", "division": "忠", "cause": "分割遺產"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_create_case_with_header_and_parties(lawyer_client):
    client, _, _ = lawyer_client
    cid = _estate_case(client)
    r = client.put(f"/api/cases/{cid}/parties", json=PARTIES)
    assert r.status_code == 200, r.text
    body = client.get(f"/api/cases/{cid}").json()
    assert body["procedure"] == "family" and body["brief_title"] == "家事準備書狀"
    assert body["addressee_default"] == "臺灣臺北地方法院家事法庭" and body["procedure_warning"] is None
    assert body["header"]["case_number"] == "113年度家繼訴字第89號" and body["header"]["division"] == "忠"
    assert [(p["seq"], p["name"], p["alignment"]) for p in body["parties"]] == [
        (1, "宋文彬", "ours"), (2, "宋文德", "opposing"), (3, "宋文琪", "aligned")]
    assert body["parties"][2]["position_note"] == "同意分割，但爭執遺產範圍"
    assert body["reference_date_label"] == "繼承開始日"


def test_patch_case_header_and_warning(lawyer_client):
    client, _, _ = lawyer_client
    cid = client.post("/api/cases", json={"title": "外遇", "our_side": "plaintiff",
                                          "case_type": "spousal_rights"}).json()["id"]
    r = client.patch(f"/api/cases/{cid}", json={"case_number": "113年度家訴字第5號", "addressee": "臺灣臺北地方法院家事法庭"})
    assert r.status_code == 200, r.text
    body = client.get(f"/api/cases/{cid}").json()
    assert body["procedure"] == "family" and body["procedure_warning"]
    assert body["header"]["addressee"] == "臺灣臺北地方法院家事法庭" and body["title"] == "外遇"
    assert client.patch(f"/api/cases/{cid}", json={"case_type": "divorce"}).status_code == 422


def test_attorneys_default_to_creator(lawyer_client, db):
    client, firm, _ = lawyer_client
    body = client.post("/api/cases", json={"title": "x", "our_side": "plaintiff"}).json()
    assert client.get(f"/api/cases/{body['id']}").json()["header"]["attorneys"] == ["lawyer律師"]
    given = client.post("/api/cases", json={"title": "y", "our_side": "plaintiff", "attorneys": ["王大明律師", "李小華律師"]}).json()
    assert client.get(f"/api/cases/{given['id']}").json()["header"]["attorneys"] == ["王大明律師", "李小華律師"]
    create_member(firm, "assistant@example.test", role="assistant")
    a = TestClient(create_app(db))
    assert login(a, "assistant@example.test").status_code == 200
    cid = a.post("/api/cases", json={"title": "z", "our_side": "plaintiff"}).json()["id"]
    assert a.get(f"/api/cases/{cid}").json()["header"]["attorneys"] == []  # 助理不是訴訟代理人


def test_party_role_and_alignment_validated(lawyer_client):
    client, _, _ = lawyer_client
    cid = _estate_case(client)
    assert client.put(f"/api/cases/{cid}/parties", json=[{"name": "甲", "role": "證人", "alignment": "ours"}]).status_code == 422
    assert client.put(f"/api/cases/{cid}/parties", json=[{"name": "甲", "role": "原告", "alignment": "friend"}]).status_code == 422
    assert client.put(f"/api/cases/{cid}/parties", json=[{"name": "", "role": "原告", "alignment": "ours"}]).status_code == 422


def test_put_parties_keeps_ids_and_rejects_foreign_party_id(lawyer_client):
    client, _, _ = lawyer_client
    cid = _estate_case(client)
    other = _estate_case(client)
    first = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    foreign = client.put(f"/api/cases/{other}/parties", json=PARTIES[:1]).json()
    kept = {**PARTIES[1], "id": first[1]["id"], "name": "宋文德（更名）"}
    r = client.put(f"/api/cases/{cid}/parties", json=[kept, PARTIES[0]])
    assert r.status_code == 200
    assert [(p["id"] == first[1]["id"], p["name"], p["seq"]) for p in r.json()] == [
        (True, "宋文德（更名）", 1), (False, "宋文彬", 2)]
    bad = client.put(f"/api/cases/{cid}/parties", json=[{**PARTIES[0], "id": foreign[0]["id"]}])
    assert bad.status_code == 422
    assert [p["name"] for p in client.get(f"/api/cases/{cid}").json()["parties"]] == ["宋文德（更名）", "宋文彬"]


def test_header_fields_too_long_rejected(lawyer_client):
    client, _, _ = lawyer_client
    r = client.post("/api/cases", json={"title": "x", "our_side": "plaintiff", "case_number": "字" * 101})
    assert r.status_code == 422
    cid = _estate_case(client)
    assert client.put(f"/api/cases/{cid}/parties", json=[{"name": "甲" * 201, "role": "原告", "alignment": "ours"}]).status_code == 422


def _upload(client, cid, round_id, filed_by=None):
    data = {"kind": "opponent_brief", "round_id": round_id}
    if filed_by:
        data["filed_by_party_id"] = filed_by
    return client.post(f"/api/cases/{cid}/documents", data=data,
                       files={"file": ("答辯狀.txt", "被告主張原告請求無理由等語。".encode(), "text/plain")})


def test_filed_by_must_belong_to_case(lawyer_client):
    client, _, _ = lawyer_client
    cid, other = _estate_case(client), _estate_case(client)
    foreign = client.put(f"/api/cases/{other}/parties", json=PARTIES).json()
    rid = client.post(f"/api/cases/{cid}/rounds").json()["id"]
    assert _upload(client, cid, rid, foreign[1]["id"]).status_code == 422


def test_removing_party_clears_filed_by(lawyer_client):
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    parties = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    rid = client.post(f"/api/cases/{cid}/rounds").json()["id"]
    up = _upload(client, cid, rid, parties[1]["id"])
    assert up.status_code == 200, up.text
    docs = client.get(f"/api/cases/{cid}").json()["documents"]
    assert docs[0]["filed_by_party_id"] == parties[1]["id"]
    assert client.put(f"/api/cases/{cid}/parties", json=[{**PARTIES[0], "id": parties[0]["id"]}]).status_code == 200
    with tenant_session(firm) as s:
        assert s.get(Document, s.scalars(select(Document.id).where(Document.case_id == cid)).one()).filed_by_party_id is None
    from tests.test_drafting import fake_all
    client.app.state.provider_factory = lambda settings, f: fake_all()
    assert client.post(f"/api/rounds/{rid}/run").status_code == 202  # 遞狀人變空白不擋執行


def test_case_context_lists_parties_and_rule(lawyer_client):
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    client.put(f"/api/cases/{cid}/parties", json=PARTIES)
    with tenant_session(firm) as s:
        text = case_context(s, s.get(Case, cid))
    assert "宋文彬" in text and "宋文德" in text and "宋文琪" in text
    assert "對造" in text and "立場與我方相同" in text and "同意分割，但爭執遺產範圍" in text
    assert "不得反駁" in text


def test_claims_step_marks_the_filer(lawyer_client):
    from app.ai.provider import AIResult  # noqa: F401
    from tests.fakes.fake_provider import FakeProvider
    from app.pipeline.claims import extract_claims
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    parties = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    rid = client.post(f"/api/cases/{cid}/rounds").json()["id"]
    assert _upload(client, cid, rid, parties[1]["id"]).status_code == 200
    fake = FakeProvider({"claims": {"claims": []}})
    with tenant_session(firm) as s:
        extract_claims(s, rid, fake, None)
    assert "【遞狀人：宋文德】" in fake.calls[0]["user"]


def test_import_testcase_reads_header(db):
    create_firm("內部測試所")
    case_id, round_id = import_testcase(CASES / "estate-2", "內部測試所")
    with admin_session() as s:
        case = s.get(Case, case_id)
        assert case.case_type == "estate_partition" and case.case_number and case.court and case.attorneys
        parties = s.scalars(select(CaseParty).where(CaseParty.case_id == case_id).order_by(CaseParty.seq)).all()
        assert len(parties) >= 3 and sum(p.alignment == "aligned" for p in parties) == 1
        brief = s.scalars(select(Document).where(Document.round_id == round_id, Document.kind == "opponent_brief")).one()
        assert brief.filed_by_party_id is not None
        assert s.get(CaseParty, brief.filed_by_party_id).alignment == "opposing"


def test_legacy_case_without_header(lawyer_client):
    client, firm, _ = lawyer_client
    with tenant_session(firm) as s:
        c = Case(firm_id=firm, title="舊案", our_side="plaintiff")
        s.add(c)
        s.flush()
        cid = str(c.id)
    body = client.get(f"/api/cases/{cid}").json()
    assert body["parties"] == [] and body["procedure"] == "civil" and body["brief_title"] == "民事準備書狀"
    assert body["header"]["case_number"] is None and body["header"]["attorneys"] == []
    assert body["addressee_default"] == "民事庭"


def test_case_options_list_types_roles_and_alignments(lawyer_client, db):
    """前端建立案件表單用：案型（名稱、程序、基準日名稱）、角色、歸屬都從後端拿，不在前端另存一份。"""
    from app.case_types import CASE_TYPES
    client, _, _ = lawyer_client
    body = client.get("/api/case-options").json()
    assert [t["code"] for t in body["case_types"]] == list(CASE_TYPES)
    assert {"code": "estate_partition", "label": "分割遺產（含特留分）", "procedure": "family",
            "reference_date_label": "繼承開始日"} in body["case_types"]
    assert body["party_roles"][:2] == ["原告", "被告"] and len(body["party_roles"]) == 8
    assert body["alignments"] == [{"code": "ours", "label": "我方"}, {"code": "opposing", "label": "對造"},
                                  {"code": "aligned", "label": "立場與我方相同"}]
    assert TestClient(create_app(db)).get("/api/case-options").status_code == 401
