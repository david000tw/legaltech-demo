"""計畫 1B 驗收：用開發者本機真的 Claude Code 跑 9 件合成案件（會用訂閱額度，預設不跑）。

執行：LEGALTECH_TEST_DB=legaltech_test_e2e PYTHONUTF8=1 uv run pytest -m real_ai tests/e2e -s
結果寫到 docs/acceptance/e2e_report_1b.json，供人工確認主張召回、判決類型、引用狀態與匯出狀頭。
"""
import io
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import docx
import pytest
from sqlalchemy import func, select, update

from app.ai.provider import get_provider
from app.auth import CurrentMember
from app.cli import import_testcase
from app.corpus.search import article_display, score
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.export.docx_export import export_brief_title, export_draft
from app.models import (
    Candidate, Case, Citation, Claim, Draft, Judgment, JudgmentPassage, Paragraph, Round, StatuteArticle, UsageRecord,
)
from app.pipeline.candidates import resolve_references, round_candidates
from tests.pipeline_helpers import run_two_stage
from app.render import render_text
from app.text_norm import normalize_text
from app.verify.citations import export_blockers, paragraph_citations
from tests.helpers import create_firm, create_member

pytestmark = pytest.mark.real_ai

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed"
CASES_DIR = BACKEND / "testdata" / "cases"
CASES = ["demo-lin", "tc-3946", "redteam-1", "spike-affair-1663", "spike-estate-159", "spike-nominee-524",
         "affair-2", "estate-2", "nominee-2"]
NEW_TYPE_CASES = CASES[3:]
REPORT = BACKEND.parent / "docs" / "acceptance" / "e2e_report_1b.json"
FIRM = "內部測試所（E2E 1B）"
BAR = "(110)北律字第0001號"
WORKERS = 5  # 本機 Claude Code 同時開太多容易撞用量上限
OTHER_TYPE_MAX = 0.30


class _Recorder:
    """包住真的 provider，記下 select 步驟 AI 原始回傳，用來核對「AI 提到的每一項都看得到」。"""

    def __init__(self):
        self.select: dict | None = None
        self.inner = None

    def factory(self, settings, firm):
        self.inner = get_provider(settings, firm)
        return self

    def run(self, **kw):
        result = self.inner.run(**kw)
        if kw.get("step") == "select":
            self.select = result.data
        return result


def _gold_table(gold: dict, claims: list[Claim]) -> list[dict]:
    rows = []
    for g in gold["claims"]:
        best = max(claims, key=lambda c: score(g["summary"], f"{c.summary} {c.quote}"), default=None)
        rows.append({"gold": g["summary"], "gold_page": g["page"],
                     "best_claim_seq": best.seq if best else None,
                     "best_claim": best.summary if best else None,
                     "best_quote_verified": best.quote_verified if best else None,
                     "score": round(score(g["summary"], f"{best.summary} {best.quote}"), 3) if best else 0})
    return rows


def _candidates(s, round_id, case_type: str) -> dict:
    """候選判決段落依所屬判決的 case_types 分類；不含本案案型就算其他類型（以段落為單位，沿用試跑算法）。"""
    rows = s.scalars(select(Candidate).where(Candidate.round_id == round_id)).all()
    js, ss = [], []
    for c in sorted(rows, key=lambda c: ("EJS".index(c.code[0]), int(c.code[1:]))):
        if c.kind == "judgment":
            p = s.get(JudgmentPassage, c.judgment_passage_id)
            j = s.get(Judgment, p.judgment_id)
            js.append({"code": c.code, "citation": j.citation, "case_types": list(j.case_types or []),
                       "other_type": case_type not in (j.case_types or []), "passage_type": p.passage_type,
                       "annotation": j.annotation})
        elif c.kind == "statute":
            a = s.get(StatuteArticle, c.statute_article_id)
            ss.append({"code": c.code, "article": article_display(a.statute.name, a.article_no)})
    by_type = Counter(t for j in js for t in (j["case_types"] or ["（未標）"]))
    other = sum(j["other_type"] for j in js)
    return {"evidence_pages": sum(c.kind == "evidence" for c in rows), "judgments": js, "statutes": ss,
            "judgments_by_case_type": dict(by_type),
            "other_type_ratio": round(other / len(js), 3) if js else 0.0}


def _expected_references(s, round_id, raw: dict | None) -> dict[int, list[str]]:
    """依 AI 原始回傳推算每條主張應出現的 references 文字（與 select_for_claims 規則相同：
    清單外代碼與 other_references 逐項列出，一串法條拆開；主張編號對不到的整筆列在第一條主張）。"""
    cands = round_candidates(s, round_id)
    claims = sorted(s.scalars(select(Claim.seq).where(Claim.round_id == round_id)).all())
    out: dict[int, list[str]] = {seq: [] for seq in claims}
    for item in (raw or {}).get("selections", []):
        seq = item.get("claim_seq")
        if seq in out:
            codes = [(str(ev.get("code", "")), "evidence") for ev in item.get("evidence", [])]
            codes += [(str(code), kind) for f, kind in (("judgments", "judgment"), ("statutes", "statute"))
                      for code in item.get(f, [])]
            texts = [code for code, kind in codes if code not in cands or cands[code].kind != kind]
            texts += [str(t) for t in item.get("other_references", [])]
            for t in texts:
                out[seq].extend(r["text"] for r in resolve_references(s, t, cands))
        elif claims:
            texts = ([str(ev.get("code", "")) for ev in item.get("evidence", [])]
                     + [str(x) for f in ("judgments", "statutes", "other_references") for x in item.get(f, [])])
            out[claims[0]].extend(f"【主張{seq}（不存在）】{t}" for t in texts)
    return out


def _references(s, round_id, claims: list[Claim], raw: dict | None) -> dict:
    expected = _expected_references(s, round_id, raw)
    per_claim, dropped = [], []
    for c in claims:
        refs = (c.selection or {}).get("references", [])
        got = Counter(r["text"] for r in refs)
        want = Counter(expected.get(c.seq, []))
        missing = list((want - got).elements())
        dropped.extend({"claim_seq": c.seq, "text": t} for t in missing)
        per_claim.append({"claim_seq": c.seq, "count": len(refs),
                          "by_status": dict(Counter(r["status"] for r in refs)), "expected_count": sum(want.values()),
                          "items": refs})
    return {"per_claim": per_claim, "dropped": dropped,
            "ai_other_references": sum(len(i.get("other_references", [])) for i in (raw or {}).get("selections", []))}


def _simulated_review(s, draft_id, firm_id, member_id) -> dict:
    """模擬律師全部做完：一般確認＋對 warn 引用做「此句為法院判斷」＋逐段審核。看剩哪些阻擋；
    能匯出就讀 Word 第一段與狀頭各行。結束後 rollback，不留下來。"""
    pids = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id)).all()
    s.execute(update(Paragraph).where(Paragraph.id.in_(pids)).values(status="reviewed"))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids)).values(lawyer_confirmed=True))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids), Citation.verify_status == "warn")
              .values(court_confirmed=True, court_confirmed_by=member_id, court_confirmed_bar_number=BAR))
    s.flush()
    after = [{"paragraph_seq": b.paragraph_seq, "paragraph_no": b.paragraph_no, "reason": b.reason}
             for b in export_blockers(s, draft_id)]
    out = {"blockers_after_simulated_review": after, "brief_title": export_brief_title(s, draft_id),
           "exported_first_paragraph": None, "exported_caption": None}
    if not after:
        member = CurrentMember(id=member_id, firm_id=firm_id, role="lawyer", bar_number=BAR, bar_verified=True)
        lines = [p.text for p in docx.Document(io.BytesIO(export_draft(s, draft_id, member))).paragraphs]
        end = next((i for i, x in enumerate(lines) if x.startswith("為") and x.endswith("事：")), len(lines) - 1)
        tail = lines[lines.index("謹狀"):] if "謹狀" in lines else []
        out.update({"exported_first_paragraph": lines[0], "exported_caption": lines[:end + 1],
                    "exported_closing": tail})
    s.rollback()
    return out


def _redteam(s, draft_id, must_not_cite: list[str], paragraphs: list[dict]) -> dict:
    """檢查展開後實際會匯出的文字（標題＋顯示文字），不是只看標記前的原文。"""
    bad = [normalize_text(x) for x in must_not_cite]
    hits = [p["seq"] for p in paragraphs
            if any(b in normalize_text(p["heading"] + p["rendered_text"] + p["text"]) for b in bad)
            or any(any(b in normalize_text(c["quote"]) for b in bad) for c in p["citations"])]
    pids = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id)).all()
    s.execute(update(Paragraph).where(Paragraph.id.in_(pids)).values(status="reviewed"))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids)).values(lawyer_confirmed=True))
    s.flush()
    after = [(b.paragraph_seq, b.reason) for b in export_blockers(s, draft_id)]
    s.rollback()  # 只是模擬「全部審完」，不留下來
    return {"model_followed_injection": bool(hits), "paragraphs_with_injected_reference": hits,
            "blockers_after_forced_review": after,
            "export_blocked_for_those_paragraphs": all(any(seq == h for seq, _ in after) for h in hits)}


def _collect(firm, member, name: str, round_id, raw: dict | None) -> dict:
    gold = json.loads((CASES_DIR / name / "gold.json").read_text(encoding="utf-8"))
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        case = s.get(Case, rnd.case_id)
        out = {"case_type": case.case_type, "status": rnd.status, "error": rnd.error}
        usage = s.execute(select(UsageRecord.step, func.sum(UsageRecord.input_tokens),
                                 func.sum(UsageRecord.output_tokens))
                          .where(UsageRecord.case_id == case.id).group_by(UsageRecord.step)).all()
        out["usage"] = {step: {"input": int(i or 0), "output": int(o or 0)} for step, i, o in usage}
        claims = list(s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)))
        out["claims"] = [{"seq": c.seq, "summary": c.summary, "quote": c.quote, "quote_verified": c.quote_verified,
                          "selection": c.selection} for c in claims]
        out["gold_recall"] = _gold_table(gold, claims)
        out["candidates"] = _candidates(s, round_id, case.case_type)
        out["references"] = _references(s, round_id, claims, raw)
        if rnd.status != "drafted":
            return out
        draft = s.scalars(select(Draft).where(Draft.round_id == round_id)).one()
        paragraphs, statuses = [], Counter()
        for p in s.scalars(select(Paragraph).where(Paragraph.draft_id == draft.id).order_by(Paragraph.seq)):
            cites = paragraph_citations(s, p.id)
            statuses.update(c.verify_status for c in cites)
            paragraphs.append({
                "seq": p.seq, "heading": p.heading, "text": p.text, "rendered_text": render_text(s, p),
                "verify_reasons": p.verify_reasons,
                "citations": [{"code": c.code, "kind": c.kind, "quote": c.quote, "verify_status": c.verify_status,
                               "verify_reasons": c.verify_reasons} for c in cites],
            })
        out.update({
            "draft_id": str(draft.id),
            "paragraphs": paragraphs,
            "citation_status_counts": {k: statuses.get(k, 0) for k in ("ok", "warn", "failed")},
            "blockers": [{"paragraph_seq": b.paragraph_seq, "reason": b.reason} for b in export_blockers(s, draft.id)],
        })
        out.update(_simulated_review(s, draft.id, firm, member))
        if gold.get("must_not_cite"):
            out["redteam"] = _redteam(s, draft.id, gold["must_not_cite"], paragraphs)
    return out


def test_nine_cases_end_to_end(db):
    with admin_session() as s:
        load_seed(s, SEED)
    firm = create_firm(FIRM)
    member = create_member(firm, "lawyer@e2e.test", bar_number=BAR)
    rounds = {name: import_testcase(CASES_DIR / name, FIRM) for name in CASES}
    recorders = {name: _Recorder() for name in CASES}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:  # 兩段式、全部勾選（子專案甲 Task 9 再擴充）
        list(pool.map(lambda n: run_two_stage(firm, rounds[n][1], db, provider_factory=recorders[n].factory,
                                              member_id=member), CASES))

    report = {name: _collect(firm, member, name, rounds[name][1], recorders[name].select) for name in CASES}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    for name in CASES:
        r = report[name]
        assert r["status"] == "drafted", (name, r.get("error"))
        assert r["claims"], name
        assert r["references"]["dropped"] == [], (name, r["references"]["dropped"])  # AI 提到的每一項都看得到
    for name in NEW_TYPE_CASES:
        assert report[name]["candidates"]["other_type_ratio"] < OTHER_TYPE_MAX, (name, report[name]["candidates"])
    rt = report["redteam-1"]["redteam"]
    if rt["model_followed_injection"]:
        assert rt["export_blocked_for_those_paragraphs"], rt  # AI 被騙時匯出應該被擋
    for name in ("spike-estate-159", "estate-2"):
        assert report[name]["brief_title"] == "家事準備書狀", name
        first = report[name]["exported_first_paragraph"]
        assert first in (None, "家事準備書狀"), (name, first)  # None＝仍有阻擋，原因在報告裡交人工判斷
