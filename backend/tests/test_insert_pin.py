"""子專案丙 Task 3：把釘選的判決插進草稿段落（設計 5.9）。插入一律走 edit_paragraph，現行審核規則全部適用。"""
import itertools
import re
import threading
from datetime import date

from sqlalchemy import func, select, update

from app.auth import CurrentMember
from app.db import admin_session, tenant_session
from app.main import create_app
from app.models import Candidate, Citation, Judgment, JudgmentPassage, Paragraph, PinnedPassage
from app.review import insert as insert_mod
from app.review.insert import insert_pin
from app.verify.citations import paragraph_blockers, paragraph_citations
from tests.export_helpers import clients
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.verify_helpers import J_REASONING, make_paragraph, setup_verify_round

QUOTE_RULE = "請在判決段落中選取至少 15 字的原文作為引文"
J1_TEXT = "{{quote:J1}}（{{cite:J1}}）"
_CJK20 = re.compile(r"[一-鿿]{20}")


SYNTH = ("本院審酌被告駕駛自用小客車行經交岔路口時未減速慢行致生本件事故應負過失責任。",
         "本院認系爭車輛之維修費用應扣除零件折舊後始為必要之修復費用故予准許。")
_seq = itertools.count(1)


def _other_reasoning(ctx, skip=()) -> tuple[str, str]:
    """回傳（段落 id, 取自原文的 20 字）：建一則合成判決的法院判斷段（不在本輪候選裡）。
    不從種子隨機挑：有些段落的句子會被驗證判成「可能是當事人主張」（warn），結果不固定。"""
    n = next(_seq)
    text = SYNTH[len(skip) % len(SYNTH)]
    with admin_session() as s:
        j = Judgment(citation=f"測試法院113年度插字第{n}號民事判決", court="測試法院", court_level="district",
                     decided_on=date(2024, 1, 1), source_url="https://example.invalid/", full_text=text,
                     status="active", curated=False)
        s.add(j)
        s.flush()
        p = JudgmentPassage(judgment_id=j.id, seq=1, passage_type="court_reasoning", text=text, status="active")
        s.add(p)
        s.flush()
        return str(p.id), _CJK20.search(text).group(0)


def _pin(firm, case_id, passage_id) -> str:
    with tenant_session(firm) as s:
        pin = PinnedPassage(firm_id=firm, case_id=case_id, judgment_passage_id=passage_id)
        s.add(pin)
        s.flush()
        return str(pin.id)


def _setup(db_):
    ctx = setup_verify_round()
    c = clients(ctx, db_)
    return ctx, c


def _para(pid):
    with admin_session() as s:
        p = s.get(Paragraph, pid)
        return p.text, p.status


def _cand_count(round_id) -> int:
    with admin_session() as s:
        return s.scalar(select(func.count()).select_from(Candidate).where(Candidate.round_id == round_id))


def test_insert_adds_candidate_and_resets_review(db):
    ctx, c = _setup(db)
    para = make_paragraph(ctx, "被告未注意車前狀況，應負賠償責任。", [])
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(Paragraph).where(Paragraph.id == para).values(status="reviewed"))
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    r = c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q})
    assert r.status_code == 200, r.text
    assert r.json() == {"paragraph_id": str(para), "code": "J3"}
    text, status = _para(para)
    assert status == "unreviewed"
    assert text.endswith("{{quote:J3}}（{{cite:J3}}）")
    with tenant_session(ctx["firm"]) as s:
        cand = s.scalars(select(Candidate).where(Candidate.round_id == ctx["round_id"], Candidate.code == "J3")).one()
        assert str(cand.judgment_passage_id) == passage and cand.pinned is True
        cit = next(x for x in paragraph_citations(s, para) if x.code == "J3")
        assert cit.verify_status == "ok" and cit.quote == q
        blockers = paragraph_blockers(s, s.get(Paragraph, para))
    assert "judgment_unconfirmed:J3" in blockers and "citation_failed:J3" not in blockers


def test_insert_reuses_existing_candidate_code(db):
    ctx, c = _setup(db)
    para = make_paragraph(ctx, "被告未注意車前狀況。", [])
    pin = _pin(ctx["firm"], ctx["case_id"], ctx["j1"])
    before = _cand_count(ctx["round_id"])
    r = c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": J_REASONING})
    assert r.status_code == 200 and r.json()["code"] == "J1"
    assert _cand_count(ctx["round_id"]) == before
    assert _para(para)[0].endswith(J1_TEXT)


def test_insert_requires_quote_from_passage(db):
    ctx, c = _setup(db)
    para = make_paragraph(ctx, "被告未注意車前狀況。", [])
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    before_text, before_count = _para(para)[0], _cand_count(ctx["round_id"])
    for bad in ("", "   ", q[:14], "這一句二十個字完全不在判決原文裡面的內容喔"):
        r = c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": bad})
        assert r.status_code == 422 and r.json()["detail"] == QUOTE_RULE, bad
    assert _para(para)[0] == before_text and _cand_count(ctx["round_id"]) == before_count


def test_insert_resets_existing_confirmations(db):
    ctx, c = _setup(db)
    para = make_paragraph(ctx, "按" + J1_TEXT + "。", [("J1", J_REASONING)])
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(Citation).where(Citation.paragraph_id == para).values(lawyer_confirmed=True))
        s.execute(update(Paragraph).where(Paragraph.id == para).values(status="reviewed"))
        assert "judgment_unconfirmed:J1" not in paragraph_blockers(s, s.get(Paragraph, para))
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    assert c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q}).status_code == 200
    with tenant_session(ctx["firm"]) as s:
        cits = {x.code: x for x in paragraph_citations(s, para)}
        assert set(cits) == {"J1", "J3"}
        assert cits["J1"].lawyer_confirmed is False and cits["J1"].quote == J_REASONING
        blockers = paragraph_blockers(s, s.get(Paragraph, para))
    assert {"judgment_unconfirmed:J1", "judgment_unconfirmed:J3", "unreviewed"} <= set(blockers)
    r = c["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409


def test_insert_rejects_statement_duplicate_and_other_case(db):
    ctx, c = _setup(db)
    with tenant_session(ctx["firm"]) as s:
        st = Paragraph(firm_id=ctx["firm"], draft_id=ctx["draft_id"], seq=0, heading="", text="被告應給付原告。",
                       kind="statement")
        s.add(st)
        s.flush()
        statement = st.id
    para = make_paragraph(ctx, "被告未注意車前狀況。", [])
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    r = c["lawyer"].post(f"/api/paragraphs/{statement}/insert-pin", json={"pin_id": pin, "quote": q})
    assert r.status_code == 422 and r.json()["detail"] == "聲明不能插入判決"
    assert c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q}).status_code == 200
    r = c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q})
    assert r.status_code == 409 and r.json()["detail"] == "本段已引用這段判決"
    other_case, _ = make_round(ctx["firm"])
    other_pin = _pin(ctx["firm"], other_case, passage)
    r = c["lawyer"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": other_pin, "quote": q})
    assert r.status_code == 422 and r.json()["detail"] == "這則釘選不屬於這個案件"


def test_concurrent_inserts_get_distinct_codes(db, monkeypatch):
    ctx = setup_verify_round()
    member_id = create_member(ctx["firm"], "lawyer@a.test")
    member = CurrentMember(id=member_id, firm_id=ctx["firm"], role="lawyer", bar_number="(110)北律字第0001號",
                           bar_verified=True)
    p1 = make_paragraph(ctx, "第一段。", [], seq=1)
    p2 = make_paragraph(ctx, "第二段。", [], seq=2)
    a, qa = _other_reasoning(ctx)
    b, qb = _other_reasoning(ctx, skip={a})
    pins = [(p1, _pin(ctx["firm"], ctx["case_id"], a), qa), (p2, _pin(ctx["firm"], ctx["case_id"], b), qb)]
    barrier = threading.Barrier(2)
    real = insert_mod._next_judgment_code

    def gated(cands):
        code = real(cands)
        try:  # 沒有鎖的話，兩邊會在這裡同時算出 J3
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        return code

    monkeypatch.setattr(insert_mod, "_next_judgment_code", gated)
    codes, errors = [], []

    def run(para, pin, q):
        try:
            with tenant_session(ctx["firm"]) as s:
                codes.append(insert_pin(s, para, pin, q, member)[1])
        except Exception as e:  # noqa: BLE001 - 測試要看到任何例外
            errors.append(e)

    threads = [threading.Thread(target=run, args=x) for x in pins]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert sorted(codes) == ["J3", "J4"]


def test_insert_other_firm_404(db):
    from fastapi.testclient import TestClient
    ctx = setup_verify_round()
    para = make_paragraph(ctx, "被告未注意車前狀況。", [])
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    other = create_firm("乙事務所")
    create_member(other, "b@b.test")
    client = TestClient(create_app(db))
    assert login(client, "b@b.test").status_code == 200
    assert client.post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q}).status_code == 404
    other_case, _ = make_round(other)
    other_pin = _pin(other, other_case, passage)
    mine = clients(ctx, db)["lawyer"]
    assert mine.post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": other_pin, "quote": q}).status_code == 404


def test_assistant_can_insert(db):
    ctx, c = _setup(db)
    para = make_paragraph(ctx, "被告未注意車前狀況。", [])
    passage, q = _other_reasoning(ctx)
    pin = _pin(ctx["firm"], ctx["case_id"], passage)
    r = c["assistant"].post(f"/api/paragraphs/{para}/insert-pin", json={"pin_id": pin, "quote": q})
    assert r.status_code == 200 and r.json()["code"] == "J3"
    with admin_session() as s:
        assert s.get(Judgment, s.get(JudgmentPassage, passage).judgment_id) is not None
