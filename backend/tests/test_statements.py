"""子專案甲 Task 4：訴之聲明、答辯聲明（聲明區塊）與 Word 依書狀種類排版（設計 5.1～5.3）。"""
import pytest
import io
from datetime import datetime, timezone

import docx
from sqlalchemy import select

from app.db import tenant_session
from app.models import Case, CaseParty, Claim, Draft, Member, Paragraph, Round
from app.render import render_text
from app.statements import create_statement, statement_of
from app.verify.citations import Blocker, export_blockers, verify_paragraph
from tests.export_helpers import clients, force_reviewed
from tests.test_export import TEXT
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

TWO = (("林大明", "原告", "ours"), ("陳志豪", "被告", "opposing"))
DEMO_AMOUNTS = (45000, 160000, 50000, 150000)


def make_brief(db, *, kind="complaint", case_type="traffic_tort", amounts=DEMO_AMOUNTS, non_money=0, parties=TWO,
               claim_value=None, body=True):
    ctx = setup_verify_round()
    cs = clients(ctx, db)
    with tenant_session(ctx["firm"]) as s:
        lawyer = s.scalars(select(Member).where(Member.email == "lawyer@a.test")).one().id
        rnd = s.get(Round, ctx["round_id"])
        rnd.brief_kind, rnd.claim_value = kind, claim_value
        case = s.get(Case, ctx["case_id"])
        case.cause, case.case_type, case.attorneys = "損害賠償", case_type, ["王大明律師"]
        for i, (name, role, alignment) in enumerate(parties, start=1):
            s.add(CaseParty(firm_id=ctx["firm"], case_id=ctx["case_id"], seq=i, name=name, role=role,
                            alignment=alignment))
        now = datetime.now(timezone.utc)
        items = [("monetary", a) for a in amounts] + [("non_monetary", None)] * non_money
        for i, (rtype, amount) in enumerate(items, start=1):
            s.add(Claim(firm_id=ctx["firm"], round_id=ctx["round_id"], seq=i, summary=f"請求項目{i}", quote="",
                        kind="request", request_type=rtype, amount=amount, confirmed_by=lawyer, confirmed_at=now))
    if body:
        make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)], heading="關於路權")
    with tenant_session(ctx["firm"]) as s:
        create_statement(s, s.get(Draft, ctx["draft_id"]))
    return ctx, cs


def _statement(ctx):
    with tenant_session(ctx["firm"]) as s:
        st = statement_of(s, ctx["draft_id"])
        return (st.id, st.text, st.kind, st.seq) if st else None


def _blockers(ctx):
    with tenant_session(ctx["firm"]) as s:
        return export_blockers(s, ctx["draft_id"])


def _edit(cs, pid, text, who="lawyer"):
    r = cs[who].patch(f"/api/paragraphs/{pid}", json={"text": text, "citations": []})
    assert r.status_code == 200, r.text


def test_statement_created_for_complaint(db):
    ctx, _ = make_brief(db)
    _, text, kind, seq = _statement(ctx)
    assert kind == "statement" and seq == 0
    assert text.startswith("一、被告應給付原告新臺幣{{amount}}元")


def test_statement_created_for_answer(db):
    ctx, _ = make_brief(db, kind="answer")
    # 測試用的對造書狀沒有聲請假執行：第一項不寫「及假執行之聲請」
    assert _statement(ctx)[1] == "一、原告之訴駁回。\n二、訴訟費用由原告負擔。\n三、如受不利判決，被告願供擔保請准宣告免為假執行。"


@pytest.mark.parametrize("brief,first", [
    ("訴之聲明：被告應給付原告新臺幣150,000元。訴訟費用由被告負擔。", "一、原告之訴駁回。"),
    ("訴之聲明：被告應給付原告新臺幣150,000元。原告願供擔保，請准宣告假執行。", "一、原告之訴及假執行之聲請均駁回。"),
    # 共同被告的答辯狀裡的「免為假執行」「假執行之聲請均駁回」不是原告聲請假執行
    ("答辯聲明：原告之訴及假執行之聲請均駁回。如受不利判決，被告願供擔保請准宣告免為假執行。", "一、原告之訴駁回。"),
])
def test_answer_statement_follows_the_opponents_provisional_request(db, brief, first):
    """答辯聲明依對造書狀有沒有聲請假執行（規則判斷，不用 AI）。"""
    from app.ingest.service import ingest_document
    ctx = setup_verify_round()
    with tenant_session(ctx["firm"]) as s:
        s.get(Round, ctx["round_id"]).brief_kind = "answer"
        ingest_document(s, ctx["firm"], ctx["case_id"], "opponent_brief", "起訴狀.txt", brief.encode("utf-8"),
                        round_id=ctx["round_id"])
    with tenant_session(ctx["firm"]) as s:
        create_statement(s, s.get(Draft, ctx["draft_id"]))
    assert _statement(ctx)[1].split(chr(10))[0] == first


def test_answer_statement_sees_a_provisional_request_in_an_earlier_brief(db):
    """起訴狀在別的輪次（或沒有輪次）聲請了假執行，這一輪的書狀沒重複寫：仍要寫「及假執行之聲請」。"""
    from app.ingest.service import ingest_document
    ctx = setup_verify_round()
    with tenant_session(ctx["firm"]) as s:
        s.get(Round, ctx["round_id"]).brief_kind = "answer"
        ingest_document(s, ctx["firm"], ctx["case_id"], "opponent_brief", "起訴狀.txt",
                        "訴之聲明：被告應給付原告新臺幣150,000元。原告願供擔保，請准宣告假執行。".encode("utf-8"))
    with tenant_session(ctx["firm"]) as s:
        create_statement(s, s.get(Draft, ctx["draft_id"]))
    assert _statement(ctx)[1].split(chr(10))[0] == "一、原告之訴及假執行之聲請均駁回。"


def test_no_statement_for_preparatory(db):
    ctx, _ = make_brief(db, kind="preparatory")
    assert _statement(ctx) is None


def test_draft_round_creates_statement_for_answer(db):
    from tests.helpers import create_firm
    from tests.pipeline_helpers import make_round, run_two_stage
    from tests.test_drafting import fake_all
    from tests.verify_helpers import EVIDENCE_TEXT
    firm = create_firm("甲事務所")
    _, rid = make_round(firm, our_side="defendant", evidence={"01_研判表.txt": EVIDENCE_TEXT})
    with tenant_session(firm) as s:
        s.get(Round, rid).brief_kind = "answer"
    run_two_stage(firm, rid, db, provider=fake_all())
    with tenant_session(firm) as s:
        draft = s.scalars(select(Draft).where(Draft.round_id == rid)).one()
        st = statement_of(s, draft.id)
        assert st is not None and st.text.startswith("一、原告之訴駁回。")


def test_statement_not_numbered(db):
    ctx, cs = make_brief(db)
    body = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()
    assert body["statement"]["kind"] == "statement" and body["statement_label"] == "訴之聲明"
    assert body["body_label"] == "事實及理由"
    assert [p["display_heading"] for p in body["paragraphs"]] == ["㈠關於路權"]
    unreviewed = [b for b in body["blockers"] if b["reason"] == "unreviewed"]
    assert {"paragraph_seq": 1, "paragraph_no": 1, "reason": "unreviewed", "scope": "paragraph"} in unreviewed
    assert {"paragraph_seq": 0, "paragraph_no": 0, "reason": "unreviewed", "scope": "statement"} in unreviewed


def test_emptied_statement_blocks_export(db):
    ctx, cs = make_brief(db)
    _edit(cs, _statement(ctx)[0], "")
    assert Blocker(0, "statement_missing", 0, "draft") in _blockers(ctx)
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["statement"] is not None


def test_amount_marker_not_in_body(db):
    ctx, _ = make_brief(db)
    pid = make_paragraph(ctx, "合計新臺幣{{amount}}元。", [], heading="關於金額", seq=2)
    with tenant_session(ctx["firm"]) as s:
        assert "amount_marker_misplaced" in verify_paragraph(s, s.get(Paragraph, pid))


def test_amount_marker_not_in_answer_statement(db):
    ctx, cs = make_brief(db, kind="answer")
    _edit(cs, _statement(ctx)[0], "一、原告之訴駁回，新臺幣{{amount}}元。")
    assert Blocker(0, "amount_marker_misplaced", 0, "statement") in _blockers(ctx)


def test_amount_marker_renders_confirmed_total(db):
    ctx, _ = make_brief(db)
    with tenant_session(ctx["firm"]) as s:
        assert "被告應給付原告新臺幣405,000元，" in render_text(s, statement_of(s, ctx["draft_id"]))


def test_blank_and_missing_amount_block_export(db):
    ctx, cs = make_brief(db)
    pid, text, _, _ = _statement(ctx)
    _edit(cs, pid, text.replace("訴訟費用", "○○訴訟費用"))
    assert Blocker(0, "statement_blank", 0, "statement") in _blockers(ctx)
    _edit(cs, pid, text.replace("{{amount}}", "405,000"))
    assert Blocker(0, "statement_amount_missing", 0, "statement") in _blockers(ctx)


def test_two_plaintiffs_need_lawyer_ack(db):
    three = (("林大明", "原告", "ours"), ("林小明", "原告", "ours"), ("陳志豪", "被告", "opposing"))
    ctx, cs = make_brief(db, parties=three)
    pid, text, _, _ = _statement(ctx)
    assert "新臺幣○○元" in text and "{{amount}}" not in text
    reasons = {b.reason for b in _blockers(ctx) if b.scope == "statement"}
    assert {"statement_blank", "amounts_unchecked"} <= reasons
    _edit(cs, pid, text.replace("新臺幣○○元", "原告林大明新臺幣200,000元、原告林小明新臺幣205,000元"))
    assert "amounts_unchecked" in {b.reason for b in _blockers(ctx)}
    assert cs["assistant"].post(f"/api/rounds/{ctx['round_id']}/amounts-checked").status_code == 403
    assert cs["lawyer"].post(f"/api/rounds/{ctx['round_id']}/amounts-checked").status_code == 200
    assert "amounts_unchecked" not in {b.reason for b in _blockers(ctx)}
    _edit(cs, pid, "一、被告應給付原告林大明新臺幣200,000元。")
    assert "amounts_unchecked" in {b.reason for b in _blockers(ctx)}


def test_statement_rewrite_refused(db):
    ctx, cs = make_brief(db)
    r = cs["lawyer"].post(f"/api/paragraphs/{_statement(ctx)[0]}/rewrite", json={"instruction": "改短"})
    assert r.status_code == 422 and "聲明不能用 AI 重寫" in r.text


def test_estate_complaint_without_value_blocked(db):
    ctx, cs = make_brief(db, case_type="estate_partition", amounts=(), non_money=1)
    assert Blocker(0, "claim_value_missing", 0, "draft") in _blockers(ctx)
    assert "{{amount}}" not in _statement(ctx)[1] and "假執行" not in _statement(ctx)[1]
    assert cs["lawyer"].patch(f"/api/rounds/{ctx['round_id']}", json={"claim_value": 2250000}).status_code == 200
    assert Blocker(0, "claim_value_missing", 0, "draft") not in _blockers(ctx)


def test_draft_view_shows_claim_value(db):
    # 草稿頁要能看到並填寫訴訟標的金額／價額（只有非金錢請求時沒有預設值，要律師填）
    ctx, cs = make_brief(db, case_type="estate_partition", amounts=(), non_money=1)
    url = f"/api/drafts/{ctx['draft_id']}"
    assert cs["lawyer"].get(url).json()["claim_value"] == {"label": "訴訟標的價額", "value": None}
    cs["lawyer"].patch(f"/api/rounds/{ctx['round_id']}", json={"claim_value": 2255150})
    assert cs["lawyer"].get(url).json()["claim_value"] == {"label": "訴訟標的價額", "value": 2255150}


def test_draft_view_claim_value_defaults_to_total(db):
    ctx, cs = make_brief(db)
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["claim_value"] == {
        "label": "訴訟標的金額", "value": 405000}


def test_draft_view_no_claim_value_for_other_kinds(db):
    ctx, cs = make_brief(db, kind="answer")
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["claim_value"] is None


def _export_lines(ctx, cs):
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200, r.text
    d = docx.Document(io.BytesIO(r.content))
    return [p.text for p in d.paragraphs if p.text.strip()], d


def test_export_complaint_layout(db):
    ctx, cs = make_brief(db)
    lines, _ = _export_lines(ctx, cs)
    assert lines[:9] == [
        "民事起訴狀", "案號：（尚未分案）　股別：", "原告　林大明", "訴訟代理人　王大明律師", "被告　陳志豪",
        "訴訟標的金額：新臺幣405,000元", "為損害賠償事件，依法起訴事：", "訴之聲明",
        "一、被告應給付原告新臺幣405,000元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。",
    ]
    assert lines[9:13] == ["二、訴訟費用由被告負擔。", "三、原告願供擔保，請准宣告假執行。", "事實及理由", "㈠關於路權"]


def test_export_answer_layout(db):
    ctx, cs = make_brief(db, kind="answer", parties=(("林大明", "原告", "opposing"), ("陳志豪", "被告", "ours")))
    lines, _ = _export_lines(ctx, cs)
    assert lines[0] == "民事答辯狀" and "為損害賠償事件，依法提出答辯事：" in lines
    i = lines.index("答辯聲明")
    assert lines[i + 1] == "一、原告之訴駁回。" and lines[i + 4] == "答辯理由" and lines[i + 5] == "㈠關於路權"
    assert not any(line.startswith("訴訟標的") for line in lines)


def test_export_estate_complaint_has_blank_schedule(db):
    ctx, cs = make_brief(db, case_type="estate_partition", amounts=(), non_money=1, claim_value=2250000)
    pid, text, _, _ = _statement(ctx)
    _edit(cs, pid, text.replace("○○○", "湯阿土"))
    lines, d = _export_lines(ctx, cs)
    assert lines[0] == "家事起訴狀" and "訴訟標的價額：新臺幣2,250,000元" in lines
    assert lines.index("謹狀") < lines.index("附表")
    (table,) = d.tables
    assert [c.text for c in table.rows[0].cells] == ["編號", "遺產項目", "價額", "分割方法", "應繼分"]
    assert len(table.rows) == 5 and all(c.text == "" for row in table.rows[1:] for c in row.cells)


def test_preparatory_export_unchanged(db):
    ctx, cs = make_brief(db, kind="preparatory", amounts=())
    lines, _ = _export_lines(ctx, cs)
    assert lines[0] == "民事準備書狀" and "為損害賠償事件，依法提出準備書狀事：" in lines
    assert "訴之聲明" not in lines and "事實及理由" not in lines


def test_draft_reports_kind_and_case_type(db):
    """前端依此顯示分割遺產的附表提示與答辯聲明提示（設計 5.2、5.3）。"""
    ctx, cs = make_brief(db, kind="answer", case_type="estate_partition", amounts=())
    body = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()
    assert body["brief_kind"] == "answer" and body["case_type"] == "estate_partition"


def test_mixed_complaint_needs_claim_value(db):
    # 子專案甲 M2：有非金錢請求時，訴訟標的價額只能由律師填（不能拿金錢加總充數）
    ctx, cs = make_brief(db, amounts=(45000,), non_money=1)
    assert Blocker(0, "claim_value_missing", 0, "draft") in _blockers(ctx)
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["claim_value"] == {
        "label": "訴訟標的價額", "value": None}


def test_zero_amount_complaint_blocked_not_500(db):
    # 子專案甲 M5：金錢請求加總為 0，視為沒有訴訟標的金額，匯出時擋下而不是當掉
    ctx, cs = make_brief(db, amounts=(0,))
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409 and any(b["reason"] == "claim_value_missing" for b in r.json()["blockers"])
