import io
from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy import select

from app.ai.prompts import load_prompt
from app.ai.schemas import SELECT_SCHEMA
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.ingest.service import ingest_document
from app.models import Candidate, Claim, Round
from app.pipeline import candidates as cand_mod
from app.pipeline.candidates import build_candidates, select_for_claims
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

SEED = Path(__file__).resolve().parent.parent / "seed"
EVIDENCE = {
    "01_研判表.txt": "研判結果：陳志豪駕駛自小客車行經無號誌路口，未讓幹道車先行且未減速注意車前狀況。",
    "02_診斷證明書.txt": "診斷病名：左側鎖骨閉鎖性骨折。醫囑：宜休養六週，期間避免提重物及劇烈活動。",
}


def blank_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def add_claims(firm, round_id, *items):
    with tenant_session(firm) as s:
        for seq, (summary, quote) in enumerate(items, start=1):
            s.add(Claim(firm_id=firm, round_id=round_id, seq=seq, summary=summary, quote=quote,
                        quote_verified=True))


def setup_round(db_, with_seed=True):
    if with_seed:
        with admin_session() as s:
            load_seed(s, SEED)
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm, evidence=EVIDENCE)
    add_claims(firm, round_id,
               ("原告行經路口未減速，與有過失", "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備"),
               ("慰撫金過高", "原告請求慰撫金15萬元過高，請鈞院審酌兩造身分、地位及原告傷勢"))
    return firm, case_id, round_id


def codes(firm, round_id):
    with tenant_session(firm) as s:
        return {c.code: c for c in s.scalars(select(Candidate).where(Candidate.round_id == round_id))}


def test_candidates_exclude_needs_ocr_pages(db):
    firm, case_id, round_id = setup_round(db, with_seed=False)
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "evidence", "03_掃描檔.pdf", blank_pdf())
    with tenant_session(firm) as s:
        cands = build_candidates(s, round_id)
        ev = [c for c in cands if c.kind == "evidence"]
        assert [c.code for c in ev] == ["E1", "E2"]
        assert all(c.page_text_id is not None for c in ev)


def test_candidates_switch_to_search_over_150k(db, monkeypatch):
    firm, _, round_id = setup_round(db, with_seed=False)
    called = []
    real = cand_mod.search_pages
    monkeypatch.setattr(cand_mod, "estimate_tokens", lambda text: 10 ** 9)
    monkeypatch.setattr(cand_mod, "search_pages",
                        lambda s, case_id, q, k: called.append(k) or real(s, case_id, q, k))
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
    assert called == [20, 20]


def test_candidate_codes_unique_and_ordered(db):
    firm, _, round_id = setup_round(db)
    with tenant_session(firm) as s:
        cands = build_candidates(s, round_id)
        got = [c.code for c in cands]
    assert len(got) == len(set(got))
    for prefix, kind in (("E", "evidence"), ("J", "judgment"), ("S", "statute")):
        nums = [int(c.code[1:]) for c in cands if c.kind == kind]
        assert nums == list(range(1, len(nums) + 1)) and all(c.code[0] == prefix for c in cands if c.kind == kind)
    assert any(c.kind == "judgment" for c in cands) and any(c.kind == "statute" for c in cands)


def test_select_drops_unknown_codes(db):
    firm, _, round_id = setup_round(db)
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [{"code": "E1", "quote": "未讓幹道車先行且未減速注意車前狀況"}],
         "judgments": ["J1", "J99"], "statutes": ["S1", "X5"], "note": "依研判表"},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
        c1 = s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.seq == 1)).one()
        assert c1.selection["judgments"] == ["J1"]
        assert c1.selection["statutes"] == ["S1"]
        assert sorted(c1.selection["dropped"]) == ["J99", "X5"]
        assert c1.selection["evidence"] == [{"code": "E1", "quote": "未讓幹道車先行且未減速注意車前狀況"}]
    call = fake.calls[0]
    assert call["effort"] == "medium" and call["schema"] == SELECT_SCHEMA
    assert "[E1]" in call["user"] and "[J1]" in call["user"] and "[S1]" in call["user"]
    assert call["system"] == load_prompt("select")


def test_select_unverified_evidence_quote(db):
    firm, _, round_id = setup_round(db)
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [{"code": "E1", "quote": "被告完全沒有注意路口狀況就直接衝出來了"}],
         "judgments": [], "statutes": [], "note": ""},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
        c1 = s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.seq == 1)).one()
        assert c1.selection["evidence"] == []
        assert c1.selection["unverified_quotes"] == [{"code": "E1", "quote": "被告完全沒有注意路口狀況就直接衝出來了"}]


def test_select_allows_empty_judgments(db):
    firm, _, round_id = setup_round(db)
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 2, "evidence": [], "judgments": [], "statutes": [], "note": "無合適判決"},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
        rows = {c.seq: c.selection for c in s.scalars(select(Claim).where(Claim.round_id == round_id))}
    assert rows[2]["judgments"] == [] and rows[2]["dropped"] == []
    assert rows[1]["judgments"] == [] and rows[1]["evidence"] == []


def test_select_prompt_has_required_rules():
    p = load_prompt("select")
    for rule in ("只能使用候選代碼", "空陣列", "法院判斷", "逐字", "15"):
        assert rule in p
