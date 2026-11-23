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
    from app.pipeline.claims import extract_claims
    from tests.fakes.fake_provider import FakeProvider
    fake = FakeProvider({"claims": {"claims": []}})
    with tenant_session(firm) as s:
        extract_claims(s, rid, fake, None)  # 遞狀人變空白：照常拆主張，只是不標遞狀人
    assert "【遞狀人" not in fake.calls[0]["user"]


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
    assert "【遞狀人：被告 宋文德（對造）】" in fake.calls[0]["user"]  # critic N3：帶角色與歸屬


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



# ---- critic（Task 6～9） ----
from app.case_types import ALIGNED_RULE  # noqa: E402
from app.ai.prompts import load_prompt  # noqa: E402


def test_aligned_rule_is_the_same_everywhere(lawyer_client):
    """M1：立場相同的當事人一律不得反駁，沒有例外；案件背景與起草提示詞用同一句。"""
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    client.put(f"/api/cases/{cid}/parties", json=PARTIES)
    with tenant_session(firm) as s:
        text = case_context(s, s.get(Case, cid))
    assert ALIGNED_RULE in text and ALIGNED_RULE in load_prompt("draft")
    assert "除外" not in text and "除外" not in load_prompt("draft")
    assert "不得反駁" in ALIGNED_RULE and "留給律師" in ALIGNED_RULE


def test_default_attorney_not_doubled(lawyer_client):
    """L1：顯示名稱已經以「律師」結尾時不再加一次。"""
    client, _, member = lawyer_client
    from app.models import Member
    with admin_session() as s:
        s.get(Member, member).display_name = "王大明律師"
    cid = client.post("/api/cases", json={"title": "x", "our_side": "plaintiff"}).json()["id"]
    assert client.get(f"/api/cases/{cid}").json()["header"]["attorneys"] == ["王大明律師"]


def test_put_parties_rejects_duplicate_ids(lawyer_client):
    """N1：同一個 id 出現兩次 → 422，不會回傳重複的列。"""
    client, _, _ = lawyer_client
    cid = _estate_case(client)
    first = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    dup = {**PARTIES[0], "id": first[0]["id"]}
    assert client.put(f"/api/cases/{cid}/parties", json=[dup, dup]).status_code == 422
    assert len(client.get(f"/api/cases/{cid}").json()["parties"]) == 3


def test_other_firm_cannot_touch_parties_or_use_them_as_filer(lawyer_client, db):
    """測試缺口：跨事務所。乙所看不到甲所的案件，也不能把甲所的當事人當遞狀人。"""
    client, _, _ = lawyer_client
    cid = _estate_case(client)
    parties = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    other_firm = create_firm("乙事務所")
    create_member(other_firm, "other@b.test")
    other = TestClient(create_app(db))
    assert login(other, "other@b.test").status_code == 200
    assert other.put(f"/api/cases/{cid}/parties", json=PARTIES[:1]).status_code == 404
    assert other.patch(f"/api/cases/{cid}", json={"case_number": "x"}).status_code == 404
    own = other.post("/api/cases", json={"title": "乙案", "our_side": "plaintiff"}).json()["id"]
    rid = other.post(f"/api/cases/{own}/rounds").json()["id"]
    assert _upload(other, own, rid, parties[1]["id"]).status_code == 422
    assert [p["name"] for p in client.get(f"/api/cases/{cid}").json()["parties"]] == ["宋文彬", "宋文德", "宋文琪"]


def test_case_context_gives_reference_date(lawyer_client):
    """N5：基準日（依案型命名）交給 AI，時效等爭點用得到。"""
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    assert client.patch(f"/api/cases/{cid}", json={"reference_date": "2023-05-01"}).status_code == 200
    with tenant_session(firm) as s:
        assert "繼承開始日：2023-05-01" in case_context(s, s.get(Case, cid))


def test_import_testcase_reads_reference_date(db, tmp_path):
    import json as _json
    import shutil
    folder = tmp_path / "estate-x"
    shutil.copytree(CASES / "estate-2", folder)
    meta = _json.loads((folder / "case.json").read_text(encoding="utf-8"))
    meta["reference_date"] = "2023-05-01"
    (folder / "case.json").write_text(_json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    create_firm("內部測試所")
    case_id, _ = import_testcase(folder, "內部測試所")
    with admin_session() as s:
        assert s.get(Case, case_id).reference_date.isoformat() == "2023-05-01"


def test_change_filer_of_uploaded_brief(lawyer_client):
    """critic M2：上傳後看得到、也改得了遞狀人；只能選本案當事人；卷證沒有遞狀人。"""
    client, _, _ = lawyer_client
    cid, other = _estate_case(client), _estate_case(client)
    parties = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    foreign = client.put(f"/api/cases/{other}/parties", json=PARTIES).json()
    rid = client.post(f"/api/cases/{cid}/rounds").json()["id"]
    doc = _upload(client, cid, rid).json()["id"]

    def filer():
        return next(d for d in client.get(f"/api/cases/{cid}").json()["documents"] if d["id"] == doc)["filed_by_party_id"]

    assert filer() is None
    r = client.patch(f"/api/documents/{doc}", json={"filed_by_party_id": parties[2]["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["filed_by_party_id"] == parties[2]["id"] and filer() == parties[2]["id"]
    assert client.patch(f"/api/documents/{doc}", json={"filed_by_party_id": foreign[1]["id"]}).status_code == 422
    assert filer() == parties[2]["id"]
    assert client.patch(f"/api/documents/{doc}", json={"filed_by_party_id": None}).status_code == 200
    assert filer() is None
    ev = client.post(f"/api/cases/{cid}/documents", data={"kind": "evidence"},
                     files={"file": ("收據.txt", "收據內容".encode(), "text/plain")}).json()["id"]
    assert client.patch(f"/api/documents/{ev}", json={"filed_by_party_id": parties[1]["id"]}).status_code == 422
    assert client.patch("/api/documents/00000000-0000-0000-0000-000000000000",
                        json={"filed_by_party_id": None}).status_code == 404


# ---- 最終審查 I2：每條主張的提出者由系統帶入起草與對應步驟，不靠 AI 在摘要裡寫名字 ----
def test_draft_and_select_label_each_claims_filer(lawyer_client):
    from app.pipeline.candidates import select_for_claims
    from app.pipeline.claims import extract_claims
    from app.pipeline.drafting import draft_context
    from tests.fakes.fake_provider import FakeProvider
    client, firm, _ = lawyer_client
    cid = _estate_case(client)
    parties = client.put(f"/api/cases/{cid}/parties", json=PARTIES).json()
    rid = client.post(f"/api/cases/{cid}/rounds").json()["id"]
    assert _upload(client, cid, rid, parties[1]["id"]).status_code == 200  # 第 1 頁：對造宋文德
    assert _upload(client, cid, rid, parties[2]["id"]).status_code == 200  # 第 2 頁：立場相同的宋文琪
    claims = {"claims": [{"page_no": 1, "summary": "被告主張請求無理由", "quote": "被告主張原告請求無理由等語。"},
                         {"page_no": 2, "summary": "被告主張請求無理由", "quote": "被告主張原告請求無理由等語。"}]}
    with tenant_session(firm) as s:
        extract_claims(s, rid, FakeProvider({"claims": claims}), None)
    with tenant_session(firm) as s:
        text = draft_context(s, rid)
    first, second = text.split("主張2：")
    assert "提出者：被告 宋文德（對造）" in first.split("主張1：")[1]
    assert "提出者：被告 宋文琪（立場與我方相同，不得反駁）" in second
    fake = FakeProvider({"select": {"claims": []}})
    with tenant_session(firm) as s:
        select_for_claims(s, rid, fake, None)
    assert "提出者：被告 宋文琪（立場與我方相同，不得反駁）" in fake.calls[0]["user"]
