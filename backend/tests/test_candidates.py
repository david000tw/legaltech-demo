import io
from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy import select

from app.ai.prompts import load_prompt
from app.ai.schemas import select_schema
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.ingest.service import ingest_document
from app.models import Candidate, Claim, Judgment, JudgmentPassage, Round, Statute, StatuteArticle
from app.pipeline import candidates as cand_mod
from app.pipeline.candidates import build_candidates, resolve_reference, select_for_claims
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


def test_select_unknown_codes_become_references(db):
    """設計 5.7：清單外的代碼不再悄悄丟掉，改列在 references 給律師看。"""
    firm, _, round_id = setup_round(db)
    with tenant_session(firm) as s:
        build_candidates(s, round_id)
        round_codes = {k: sorted((c.code for c in s.scalars(select(Candidate).where(
            Candidate.round_id == round_id, Candidate.kind == kind))), key=lambda x: int(x[1:]))
            for k, kind in (("evidence", "evidence"), ("judgments", "judgment"), ("statutes", "statute"))}
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [{"code": "E1", "quote": "未讓幹道車先行且未減速注意車前狀況"}],
         "judgments": ["J1", "J99"], "statutes": ["S1", "X5"], "other_references": [], "note": "依研判表"},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
        c1 = s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.seq == 1)).one()
        assert c1.selection["judgments"] == ["J1"]
        assert c1.selection["statutes"] == ["S1"]
        assert c1.selection.get("dropped", []) == []
        assert {(r["text"], r["status"]) for r in c1.selection["references"]} == {("J99", "unmatched"), ("X5", "unmatched")}
        assert c1.selection["evidence"] == [{"code": "E1", "quote": "未讓幹道車先行且未減速注意車前狀況"}]
    call = fake.calls[0]
    assert call["effort"] == "medium" and call["schema"] == select_schema(round_codes)
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
    assert rows[2]["judgments"] == [] and rows[2]["references"] == []
    assert rows[1]["judgments"] == [] and rows[1]["evidence"] == []


def test_select_prompt_has_required_rules():
    p = load_prompt("select")
    for rule in ("只能使用候選代碼", "空陣列", "法院判斷", "逐字", "15", "other_references"):
        assert rule in p


# ---- 計畫 1B Task 4：只能填候選代碼，另列「想引用但不在清單」（設計 5.7） ----

def test_select_schema_restricts_codes():
    sch = select_schema({"evidence": ["E1"], "judgments": [], "statutes": ["S1", "S2"]})
    sel = sch["properties"]["selections"]["items"]
    assert sel["properties"]["statutes"]["items"]["enum"] == ["S1", "S2"]
    assert sel["properties"]["evidence"]["items"]["properties"]["code"]["enum"] == ["E1"]
    assert "judgments" not in sel["properties"] and "judgments" not in sel.get("required", [])
    assert "other_references" in sel["properties"] and "other_references" in sel["required"]


def _reference_round(db_):
    """候選：S1＝民法第184條；J1、J2＝同一判決（3946）的兩段法院判斷；J3＝另一判決唯一的一段。"""
    firm, _, round_id = setup_round(db_)
    with admin_session() as s:
        civil = s.scalars(select(Statute).where(Statute.name == "民法")).one()
        a184 = s.scalars(select(StatuteArticle).where(StatuteArticle.statute_id == civil.id,
                                                      StatuteArticle.article_no == "184")).one().id
        a191_2 = s.scalars(select(StatuteArticle).where(StatuteArticle.statute_id == civil.id,
                                                        StatuteArticle.article_no == "191-2")).one().id
        multi, single = None, None
        for j in s.scalars(select(Judgment).order_by(Judgment.citation)):
            ps = s.scalars(select(JudgmentPassage.id).where(JudgmentPassage.judgment_id == j.id,
                                                             JudgmentPassage.passage_type == "court_reasoning")).all()
            if len(ps) >= 2 and multi is None:
                multi = (j.citation, ps[0], ps[1])
            elif len(ps) >= 1 and single is None and (multi is None or j.citation != multi[0]):
                single = (j.citation, ps[0])
    with tenant_session(firm) as s:
        s.add_all([
            Candidate(firm_id=firm, round_id=round_id, code="S1", kind="statute", statute_article_id=a184),
            Candidate(firm_id=firm, round_id=round_id, code="S2", kind="statute", statute_article_id=a191_2),
            Candidate(firm_id=firm, round_id=round_id, code="J1", kind="judgment", judgment_passage_id=multi[1]),
            Candidate(firm_id=firm, round_id=round_id, code="J2", kind="judgment", judgment_passage_id=multi[2]),
            Candidate(firm_id=firm, round_id=round_id, code="J3", kind="judgment", judgment_passage_id=single[1]),
        ])
    return firm, round_id, multi[0], single[0]


def _claim1(firm, round_id):
    with tenant_session(firm) as s:
        return s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.seq == 1)).one().selection


def test_other_references_resolved(db):
    firm, round_id, multi_cit, single_cit = _reference_round(db)
    refs = ["民法第184條第1項前段", multi_cit, single_cit, "民法第1030條之1"]
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [], "judgments": [], "statutes": [], "other_references": refs, "note": ""},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
    sel = _claim1(firm, round_id)
    assert [(r["text"], r["status"], r["code"]) for r in sel["references"]] == [
        ("民法第184條第1項前段", "mapped", "S1"), (multi_cit, "ambiguous_passage", None),
        (single_cit, "mapped", "J3"), ("民法第1030條之1", "unmatched", None)]
    assert sel["statutes"] == ["S1"] and sel["judgments"] == ["J3"]


def test_names_in_code_fields_are_not_dropped(db):
    firm, round_id, _, _ = _reference_round(db)
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [], "judgments": [], "statutes": ["民法第184條", "S9"], "other_references": [],
         "note": ""},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
    sel = _claim1(firm, round_id)
    assert {"text": "民法第184條", "status": "mapped", "code": "S1"} in sel["references"]
    assert {"text": "S9", "status": "unmatched", "code": None} in sel["references"]
    assert sel["statutes"] == ["S1"]


def test_resolve_reference_handles_spacing_and_subarticles(db):
    firm, round_id, _, _ = _reference_round(db)
    with tenant_session(firm) as s:
        cands = {c.code: c for c in s.scalars(select(Candidate).where(Candidate.round_id == round_id))}
        assert resolve_reference(s, "民法 第 184 條 第 2 項", cands)["code"] == "S1"
        assert resolve_reference(s, "民法第184條之1", cands)["status"] == "unmatched"
        assert resolve_reference(s, "", cands)["status"] == "unmatched"



# ---- critic（Task 1～5）M2、M3：對錯法院、一串法條、同一主張分兩筆 ----
from app.pipeline.candidates import resolve_references  # noqa: E402


def test_court_abbreviation_is_not_trusted(db):
    firm, round_id, multi_cit, single_cit = _reference_round(db)
    import re
    number = re.search(r"\d+年度.+?字第\d+號", single_cit).group(0)
    with tenant_session(firm) as s:
        cands = {c.code: c for c in s.scalars(select(Candidate).where(Candidate.round_id == round_id))}
        # 寫了簡稱或別的法院：對不上完整法院名稱就不採用，交給律師看
        assert resolve_reference(s, "某地院" + number, cands)["status"] == "unmatched"
        assert resolve_reference(s, number, cands)["status"] == "mapped"  # 只寫字號、候選中唯一 → 對回


def test_several_articles_in_one_reference_each_resolved(db):
    firm, round_id, _, _ = _reference_round(db)
    with tenant_session(firm) as s:
        cands = {c.code: c for c in s.scalars(select(Candidate).where(Candidate.round_id == round_id))}
        got = [(r["text"], r["status"], r["code"]) for r in resolve_references(s, "民法第184條、第185條", cands)]
        assert got == [("民法第184條", "mapped", "S1"), ("民法第185條", "unmatched", None)]
        got = [(r["status"], r["code"]) for r in resolve_references(s, "民法第184條第1項前段、第191條之2", cands)]
        assert got == [("mapped", "S1"), ("mapped", "S2")]


def test_select_merges_split_items_and_keeps_unknown_claims(db):
    firm, round_id, _, single_cit = _reference_round(db)
    fake = FakeProvider({"select": {"selections": [
        {"claim_seq": 1, "evidence": [], "judgments": [], "statutes": ["S1"], "other_references": ["民法第1030條之1"],
         "note": "甲"},
        {"claim_seq": 1, "evidence": [], "judgments": ["J3"], "statutes": [], "other_references": [], "note": "乙"},
        {"claim_seq": 9, "evidence": [], "judgments": [], "statutes": ["S2"], "other_references": ["民法第195條"],
         "note": ""},
    ]}})
    with tenant_session(firm) as s:
        select_for_claims(s, round_id, fake, None)
    sel = _claim1(firm, round_id)
    assert sel["statutes"] == ["S1"] and sel["judgments"] == ["J3"]  # 兩筆合併，前一筆沒有消失
    texts = [r["text"] for r in sel["references"]]
    assert "民法第1030條之1" in texts
    assert any("主張9" in t and "S2" in t for t in texts) and any("主張9" in t and "民法第195條" in t for t in texts)
