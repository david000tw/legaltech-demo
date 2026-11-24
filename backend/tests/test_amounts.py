"""子專案甲 Task 7：書狀內金額檢查——對不上請求項目、加總或卷證的金額標黃，律師核對後放行（設計 5.5）。"""
import pytest
from sqlalchemy import select

from app.amounts import parse_amounts
from app.db import tenant_session
from app.models import Draft, Paragraph, ReviewEvent, Round
from app.statements import statement_of
from app.verify.citations import Blocker, export_blockers, verify_paragraph
from tests.export_helpers import force_reviewed
from tests.pipeline_helpers import make_round
from tests.test_statements import make_brief


@pytest.mark.parametrize("text,expected", [
    ("新臺幣45,000元", [45000]), ("45000元", [45000]), ("新臺幣（下同）405,000元", [405000]),
    ("40萬5,000元", [405000]), ("四十萬五千元", [405000]), ("肆拾萬元", [400000]), ("15萬元", [150000]),
    ("8.6萬元", [86000]), ("民國113年4月2日", []), ("第184條", []), ("每萬元130元", [130]),
    ("新臺幣 45,000 元", [45000]), ("４５，０００元", [45000]), ("肆萬伍仟元", [45000]), ("4萬5千元", [45000]),
])
def test_parse_amounts(text, expected):
    assert parse_amounts(text) == expected


def test_parse_several_amounts():
    assert parse_amounts("零件費110,000元、工資50,000元，合計16萬元") == [110000, 50000, 160000]


def _reasons(ctx):
    return {b.reason for b in _blockers(ctx)}


def _blockers(ctx):
    with tenant_session(ctx["firm"]) as s:
        return export_blockers(s, ctx["draft_id"])


def test_unverified_amount_blocks_until_confirmed(db):
    from tests.verify_helpers import make_paragraph
    ctx, cs = make_brief(db)
    ok = make_paragraph(ctx, "原告所受損害合計新臺幣405,000元，其中醫療費用4萬5,000元。", [], heading="關於損害總額", seq=2)
    bad = make_paragraph(ctx, "原告所受損害合計新臺幣406,000元。", [], heading="關於損害", seq=3)
    blockers = _blockers(ctx)
    assert Blocker(3, "amount_unverified:406000", 3) in blockers
    assert not any(b.paragraph_seq == 2 and b.reason.startswith("amount_unverified") for b in blockers)
    r = cs["lawyer"].post(f"/api/paragraphs/{bad}/confirm-amount", json={"amount": 406000})
    assert r.status_code == 200, r.text
    assert "amount_unverified:406000" not in _reasons(ctx)
    with tenant_session(ctx["firm"]) as s:
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == bad)).one()
        assert (ev.action, ev.after_text) == ("confirm_amount", "406000")
    assert ok


def _plain_draft(firm, evidence, text, heading="關於損害"):
    case_id, rid = make_round(firm, evidence=evidence)
    with tenant_session(firm) as s:
        draft = Draft(firm_id=firm, round_id=rid)
        s.add(draft)
        s.flush()
        p = Paragraph(firm_id=firm, draft_id=draft.id, seq=1, heading=heading, text=text, status="unreviewed",
                      verify_reasons=[])
        s.add(p)
        s.flush()
        return p.id


def _amount_reasons(firm, pid):
    with tenant_session(firm) as s:
        return [r for r in verify_paragraph(s, s.get(Paragraph, pid)) if r.startswith("amount_unverified")]


def test_amount_in_evidence_allowed(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {"01_收據.txt": "收據：醫療費用新臺幣 45,000 元"}, "原告支出醫療費用4萬5,000元，另支出7,000元。")
    assert _amount_reasons(firm, pid) == ["amount_unverified:7000"]


def test_amount_in_opponent_brief_allowed(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {}, "被告稱原告請求慰撫金150,000元過高，並無理由。")  # 對造書狀寫「15萬元」
    assert _amount_reasons(firm, pid) == []


def test_amount_inside_quote_not_scanned(db):
    from tests.helpers import create_firm
    from app.verify.citations import attach_citations
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {}, "此有{{quote:E1}}可稽。")
    with tenant_session(firm) as s:
        attach_citations(s, s.get(Paragraph, pid), [("E1", "原告請求賠償新臺幣999,999元，業已到期")])
    assert _amount_reasons(firm, pid) == []


def test_edit_clears_amount_confirmation(db):
    from tests.verify_helpers import make_paragraph
    ctx, cs = make_brief(db)
    pid = make_paragraph(ctx, "合計新臺幣406,000元。", [], heading="關於損害", seq=2)
    cs["lawyer"].post(f"/api/paragraphs/{pid}/confirm-amount", json={"amount": 406000})
    assert "amount_unverified:406000" not in _reasons(ctx)
    r = cs["lawyer"].patch(f"/api/paragraphs/{pid}", json={"text": "合計新臺幣406,000元整。", "citations": []})
    assert r.status_code == 200
    assert "amount_unverified:406000" in _reasons(ctx)
    with tenant_session(ctx["firm"]) as s:
        assert s.get(Paragraph, pid).amounts_confirmed == []


def test_assistant_cannot_confirm_amount(db):
    from tests.verify_helpers import make_paragraph
    ctx, cs = make_brief(db)
    pid = make_paragraph(ctx, "合計新臺幣406,000元。", [], heading="關於損害", seq=2)
    assert cs["assistant"].post(f"/api/paragraphs/{pid}/confirm-amount", json={"amount": 406000}).status_code == 403


def test_old_reviewed_draft_amounts(db):
    """Review Focus 1：舊的準備書狀草稿也要檢查金額；卷證裡有的金額照常匯出，自己算的要律師核對。"""
    from tests.verify_helpers import make_paragraph
    ctx, cs = make_brief(db, kind="preparatory", amounts=())
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200
    pid = make_paragraph(ctx, "原告各項損害合計新臺幣123,456元。", [], heading="關於損害", seq=2)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409 and any(b["reason"] == "amount_unverified:123456" for b in r.json()["blockers"])
    cs["lawyer"].post(f"/api/paragraphs/{pid}/confirm-amount", json={"amount": 123456})
    assert cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200


def test_checked_statement_not_scanned(db):
    three = (("林大明", "原告", "ours"), ("林小明", "原告", "ours"), ("陳志豪", "被告", "opposing"))
    ctx, cs = make_brief(db, parties=three)
    with tenant_session(ctx["firm"]) as s:
        st = statement_of(s, ctx["draft_id"])
        pid, text = st.id, st.text
    edited = text.replace("新臺幣○○元", "原告林大明新臺幣200,000元、原告林小明新臺幣205,000元")
    cs["lawyer"].patch(f"/api/paragraphs/{pid}", json={"text": edited, "citations": []})
    statement = {b.reason for b in _blockers(ctx) if b.scope == "statement"}
    assert {"amount_unverified:200000", "amount_unverified:205000"} <= statement
    assert cs["lawyer"].post(f"/api/rounds/{ctx['round_id']}/amounts-checked").status_code == 200
    statement = {b.reason for b in _blockers(ctx) if b.scope == "statement"}
    assert not any(r.startswith("amount_unverified") for r in statement)
    with tenant_session(ctx["firm"]) as s:
        assert s.get(Round, ctx["round_id"]).amounts_checked_by is not None


def _add_request(firm, pid, *, quote_verified, found):
    from app.models import Claim
    with tenant_session(firm) as s:
        rid = s.get(Draft, s.get(Paragraph, pid).draft_id).round_id
        s.add(Claim(firm_id=firm, round_id=rid, seq=1, summary="醫療費用", quote="醫療費用共計48,000元",
                    quote_verified=quote_verified, kind="request", request_type="monetary", amount=45000,
                    evidence=[{"quote": "醫療費用支出48,000元", "found": found}]))


def test_amount_from_unfound_quote_not_allowed(db):
    # AI 編的原文摘錄（畫面標紅「未找到原文」）不能拿來放行正文的金額（設計 5.1、5.5）
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {}, "原告支出醫療費用新臺幣48,000元。")
    _add_request(firm, pid, quote_verified=False, found=False)
    assert _amount_reasons(firm, pid) == ["amount_unverified:48000"]


def test_amount_from_found_quote_allowed(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {}, "原告支出醫療費用新臺幣48,000元。")
    _add_request(firm, pid, quote_verified=True, found=True)
    assert _amount_reasons(firm, pid) == []


def test_amount_in_heading_checked(db):
    from tests.helpers import create_firm
    firm = create_firm("甲事務所")
    pid = _plain_draft(firm, {}, "原告受有損害。", heading="醫療費用新臺幣49,000元")
    assert _amount_reasons(firm, pid) == ["amount_unverified:49000"]


def test_allowed_amounts_computed_once_per_export(db, monkeypatch):
    # 子專案甲 M4：一次匯出檢查只查一次可用金額（卷證、對造書狀、主張），不要每段重查
    from app.verify import citations
    from tests.verify_helpers import make_paragraph
    ctx, _ = make_brief(db, body=False)
    for seq, n in enumerate(("45,000", "405,000", "7,000"), start=1):
        make_paragraph(ctx, f"原告支出新臺幣{n}元。", [], heading=f"關於損害{seq}", seq=seq)
    calls = []
    real = citations._amount_sources
    monkeypatch.setattr(citations, "_amount_sources", lambda s, rnd: calls.append(1) or real(s, rnd))
    with tenant_session(ctx["firm"]) as s:
        blockers = export_blockers(s, ctx["draft_id"])
    assert len(calls) == 1
    assert Blocker(3, "amount_unverified:7000", 3) in blockers
