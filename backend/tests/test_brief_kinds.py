"""子專案甲 Task 3：書狀名稱、聲明範本、建立輪次時選書狀種類、輪次設定（設計 5.1～5.3）。"""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from sqlalchemy import select

from app.case_types import (
    allowed_brief_kinds, answer_statement, auto_money_statement, brief_title, complaint_statement,
    default_brief_kind, procedure_warning,
)
from app.db import tenant_session
from app.models import CaseParty, Round
from tests.pipeline_helpers import make_round, run_two_stage
from tests.test_drafting import fake_all
from tests.verify_helpers import EVIDENCE_TEXT

MONEY_LINE = "被告應給付原告新臺幣{{amount}}元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。"


def test_six_titles():
    got = {(p, k): brief_title(p, k) for p in ("civil", "family") for k in ("complaint", "answer", "preparatory")}
    assert got == {("civil", "complaint"): "民事起訴狀", ("civil", "answer"): "民事答辯狀",
                   ("civil", "preparatory"): "民事準備書狀", ("family", "complaint"): "家事起訴狀",
                   ("family", "answer"): "家事答辯狀", ("family", "preparatory"): "家事準備書狀"}
    assert brief_title("civil") == "民事準備書狀"  # 舊的呼叫方式照舊


def test_traffic_complaint_statement_joint():
    text = complaint_statement("traffic_tort", has_money=True, has_non_money=False, joint=True, provisional=True,
                               auto_money=True, variant=None)
    assert text.split("\n") == [
        "一、被告應連帶給付原告新臺幣{{amount}}元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。",
        "二、訴訟費用由被告負擔。",
        "三、原告願供擔保，請准宣告假執行。",
    ]
    single = complaint_statement("traffic_tort", has_money=True, has_non_money=False, joint=False, provisional=False,
                                 auto_money=True, variant=None)
    assert single.split("\n") == ["一、" + MONEY_LINE, "二、訴訟費用由被告負擔。"]


def test_estate_statement_no_provisional_execution():
    text = complaint_statement("estate_partition", has_money=False, has_non_money=True, joint=False, provisional=True,
                               auto_money=True, variant=None)
    assert text.split("\n") == [
        "一、兩造公同共有被繼承人○○○所遺如附表所示之遺產，准依附表所示方法分割。",
        "二、訴訟費用由兩造按附表所示應繼分比例負擔。",
    ]
    assert "假執行" not in text


def test_nominee_variants():
    alive = complaint_statement("nominee_registration", has_money=False, has_non_money=True, joint=False,
                                provisional=True, auto_money=True, variant="alive")
    dead = complaint_statement("nominee_registration", has_money=False, has_non_money=True, joint=False,
                               provisional=True, auto_money=True, variant="deceased")
    assert alive.split("\n")[0] == "一、被告應將坐落○○之不動產（權利範圍○○）所有權移轉登記予原告。"
    assert dead.split("\n")[0] == "一、被告應就被繼承人○○○所遺坐落○○之不動產辦理繼承登記後，將所有權移轉登記予原告。"
    assert "假執行" not in alive and "假執行" not in dead and dead.endswith("二、訴訟費用由被告負擔。")


def test_mixed_money_and_non_money():
    text = complaint_statement("nominee_registration", has_money=True, has_non_money=True, joint=False,
                               provisional=True, auto_money=True, variant=None)
    lines = text.split("\n")
    assert lines[0] == "一、" + MONEY_LINE and lines[1].startswith("二、被告應將坐落")
    assert lines[-1] == "四、就第一項聲明，原告願供擔保，請准宣告假執行。"


def test_money_line_not_auto_for_two_plaintiffs():
    def party(role, alignment):
        return CaseParty(name="x", role=role, alignment=alignment)
    two_plaintiffs = [party("原告", "ours"), party("原告", "ours"), party("被告", "opposing")]
    assert auto_money_statement(two_plaintiffs, joint=False) is False
    assert auto_money_statement([party("原告", "ours"), party("被告", "opposing"), party("被告", "opposing")],
                                joint=False) is False
    assert auto_money_statement([party("原告", "ours"), party("被告", "opposing"), party("被告", "opposing")],
                                joint=True) is True
    assert auto_money_statement([], joint=False) is True
    text = complaint_statement("traffic_tort", has_money=True, has_non_money=False, joint=False, provisional=True,
                               auto_money=False, variant=None)
    assert "新臺幣○○元" in text and "{{amount}}" not in text


def test_answer_statement_by_case_type():
    assert answer_statement("traffic_tort").split("\n") == [
        "一、原告之訴及假執行之聲請均駁回。", "二、訴訟費用由原告負擔。", "三、如受不利判決，被告願供擔保請准宣告免為假執行。"]
    estate = answer_statement("estate_partition")
    assert estate.split("\n") == ["一、原告之訴駁回。", "二、訴訟費用由原告負擔。"] and "假執行" not in estate


def test_allowed_and_default_kinds():
    assert allowed_brief_kinds("plaintiff") == ["complaint", "preparatory"]
    assert allowed_brief_kinds("defendant") == ["answer", "preparatory"]
    assert default_brief_kind("plaintiff", has_rounds=False) == "complaint"
    assert default_brief_kind("defendant", has_rounds=False) == "answer"
    assert default_brief_kind("plaintiff", has_rounds=True) == "preparatory"


def _case(client, side):
    r = client.post("/api/cases", json={"title": "測試案", "our_side": side})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_create_round_kind_rules(lawyer_client):
    client, _, _ = lawyer_client
    cid = _case(client, "plaintiff")
    body = client.get(f"/api/cases/{cid}").json()
    assert body["default_brief_kind"] == "complaint"
    assert body["brief_kind_options"] == [{"code": "complaint", "label": "起訴狀"},
                                          {"code": "preparatory", "label": "準備書狀"}]
    first = client.post(f"/api/cases/{cid}/rounds", json={}).json()
    assert client.get(f"/api/cases/{cid}").json()["rounds"][0]["brief_kind"] == "complaint"
    assert client.get(f"/api/cases/{cid}").json()["default_brief_kind"] == "preparatory"
    r = client.post(f"/api/cases/{cid}/rounds", json={"brief_kind": "answer"})
    assert r.status_code == 422 and "原告" in r.text and "答辯狀" in r.text
    dcid = _case(client, "defendant")
    r = client.post(f"/api/cases/{dcid}/rounds", json={"brief_kind": "complaint"})
    assert r.status_code == 422 and "被告" in r.text and "起訴狀" in r.text
    assert client.post(f"/api/cases/{dcid}/rounds").status_code == 200  # 沒帶內容＝預設
    rounds = client.get(f"/api/cases/{dcid}").json()["rounds"]
    assert rounds[0]["brief_kind"] == "answer" and rounds[0]["failed_stage"] is None
    assert rounds[0]["copied_from_seq"] is None and first["seq"] == 1


def test_patch_round_settings_only_before_draft(lawyer_client):
    client, firm, _ = lawyer_client
    cid = _case(client, "plaintiff")
    rid = client.post(f"/api/cases/{cid}/rounds", json={}).json()["id"]
    r = client.patch(f"/api/rounds/{rid}", json={"narrative": "請求醫療費與慰撫金", "joint_liability": True,
                                                  "provisional_execution": False, "claim_value": 405000,
                                                  "statement_variant": "deceased"})
    assert r.status_code == 200, r.text
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        assert (rnd.narrative, rnd.joint_liability, rnd.provisional_execution, rnd.claim_value,
                rnd.statement_variant) == ("請求醫療費與慰撫金", True, False, 405000, "deceased")
        rnd.status = "drafted"
    assert client.patch(f"/api/rounds/{rid}", json={"narrative": "改"}).status_code == 409
    assert client.patch(f"/api/rounds/{rid}", json={"claim_value": 500000}).status_code == 200
    with tenant_session(firm) as s:
        assert s.get(Round, rid).claim_value == 500000
    assert client.patch(f"/api/rounds/{rid}", json={"statement_variant": "other"}).status_code == 422


@pytest.mark.parametrize("status,failed_stage,minutes_ago,expected", [
    ("failed", "extract", 0, 200),      # 拆解失敗（例如卷證都是掃描檔）：補上卷證後要能改案情說明再拆解
    ("extracting", None, 46, 200),      # 拆解逾時：畫面的「重新開始」會先存設定
    ("extracting", None, 1, 409),       # 正在拆解中
    ("failed", "draft", 0, 409),        # 起草失敗：重新起草不用改設定
])
def test_patch_round_settings_when_extract_can_restart(lawyer_client, status, failed_stage, minutes_ago, expected):
    client, firm, _ = lawyer_client
    cid = _case(client, "plaintiff")
    rid = client.post(f"/api/cases/{cid}/rounds", json={}).json()["id"]
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        rnd.status, rnd.failed_stage = status, failed_stage
        rnd.started_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    r = client.patch(f"/api/rounds/{rid}", json={"narrative": "補充：請求醫療費"})
    assert r.status_code == expected, r.text


def _answer_round(firm):
    case_id, rid = make_round(firm, our_side="defendant", evidence={"01_研判表.txt": EVIDENCE_TEXT})
    with tenant_session(firm) as s:
        s.get(Round, rid).brief_kind = "answer"
    return case_id, rid


def test_answer_round_prompts_say_answer(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, rid = _answer_round(firm)
    fake = fake_all()
    run_two_stage(firm, rid, db, provider=fake)
    assert [c["step"] for c in fake.calls] == ["claims", "select", "draft"]
    for call in fake.calls:
        assert "書狀名稱：民事答辯狀" in call["user"], call["step"]
    assert fake.calls[-1]["user"].endswith("請依寫作規則，逐條回應原告起訴狀之主張，撰寫答辯狀之答辯理由段落。")


def test_preparatory_instruction_unchanged(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    _, rid = make_round(firm, evidence={"01_研判表.txt": EVIDENCE_TEXT})
    fake = fake_all()
    run_two_stage(firm, rid, db, provider=fake)
    assert fake.calls[-1]["user"].endswith("請依寫作規則，逐條回應對造主張，撰寫準備書狀段落。")


def test_export_filename_uses_kind(db):
    from tests.export_helpers import force_reviewed
    from tests.test_export import ready
    ctx, cs = ready(db)
    from app.models import Draft
    from app.statements import create_statement
    with tenant_session(ctx["firm"]) as s:
        s.get(Round, ctx["round_id"]).brief_kind = "answer"
    with tenant_session(ctx["firm"]) as s:  # 答辯狀沒有答辯聲明不能匯出（子專案甲 Task 4）
        create_statement(s, s.get(Draft, ctx["draft_id"]))
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["brief_title"] == "民事答辯狀"
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200, r.text
    assert quote("民事答辯狀.docx") in r.headers["content-disposition"]


def test_money_case_non_money_line_is_generic():
    # 子專案甲 M3：車禍、配偶權這類案型的非金錢請求，不套不動產移轉的句子，留空格讓律師寫
    text = complaint_statement("traffic_tort", has_money=True, has_non_money=True, joint=False,
                               provisional=True, auto_money=True, variant=None)
    assert "被告應○○○。" in text and "不動產" not in text


def test_procedure_warning_names_brief_kind():
    # 子專案甲 M6：程序提醒寫的書狀名稱要跟本輪書狀種類一致
    w = procedure_warning("spousal_rights", "113年度家訴字第5號", "complaint")
    assert "家事起訴狀" in w and "準備書狀" not in w
