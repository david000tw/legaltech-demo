"""spike：用開發者本機真的 Claude Code 跑三種新案型（外遇、分割遺產／特留分、借名登記）的合成案件。

這不是正式驗收，只為了看現行（為車禍設計的）流程碰到新案型時哪裡能用、哪裡會壞。
執行：LEGALTECH_TEST_DB=legaltech_test_spike PYTHONUTF8=1 uv run pytest -m real_ai tests/e2e/test_spike_case_types.py -s
結果寫到 docs/acceptance/spike_case_types.json。

檔案內另有一個不呼叫 AI 的檢查（預設測試會跑）：三件案件能匯入、對造書狀 3 頁、人名不是原判決的真名。
"""
import io
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import docx
import pytest
from sqlalchemy import func, select, update

from app.auth import CurrentMember
from app.cli import import_testcase
from app.corpus.search import article_display, score
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.export.docx_export import export_draft
from app.models import (
    Candidate, Citation, Claim, Document, Draft, Exhibit, Judgment, JudgmentPassage, Page, Paragraph, Round,
    StatuteArticle, UsageRecord,
)
from app.pipeline.claims import opponent_pages
from app.pipeline.run import run_round, start_round
from app.render import render_text
from app.verify.citations import export_blockers, paragraph_citations
from tests.helpers import create_firm, create_member

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed"
CASES_DIR = BACKEND / "testdata" / "cases"
CASES = ["spike-affair-1663", "spike-estate-159", "spike-nominee-524"]
CASE_FAMILY = {"spike-affair-1663": "外遇", "spike-estate-159": "分割遺產", "spike-nominee-524": "借名登記"}
REPORT = BACKEND.parent / "docs" / "acceptance" / "spike_case_types.json"
FIRM = "內部測試所（SPIKE）"

# 種子判決 → 案型（用來看候選判決有沒有混到別的案型）
SEED_FAMILY = {
    "tcev_113_3946": "車禍", "TPEV_114_beijian_610": "車禍", "SDEV_114_shajian_456": "車禍",
    "TCDV_113_jianshang_211": "車禍", "SCDV_114_jianshang_17": "車禍",
    "TPDV_113_su_1663": "外遇", "TYDV_113_su_2562": "外遇", "TNDV_114_su_1347": "外遇",
    "KSYV_112_jiajisu_159": "分割遺產", "CHDV_113_jiajisu_15": "分割遺產", "PCDV_112_jiajisu_116": "分割遺產",
    "TPDV_113_jiajisu_89": "分割遺產",
    "PCDV_113_chongsu_524": "借名登記", "TCEV_113_zhongjian_3211": "借名登記", "PCDV_113_chongsu_548": "借名登記",
}
# 原判決的真實姓名（合成案件不得出現）
REAL_NAMES = {
    "spike-affair-1663": ["林柏齡", "陳貴城", "楊嘉琪", "連靜宜", "盧怡璇", "趙佾慧"],
    "spike-estate-159": ["林石猛", "梁郁茌", "陳永祥", "楊士弘", "黃吉榮"],
    "spike-nominee-524": ["呂玉蓮", "陳宇暉", "陳銘紳", "陳俊安", "陳淑玲", "陳俊傑", "呂傳茂", "呂芳野"],
}
# 草稿出現這些字，表示提示詞或檢索把車禍的框架帶進新案型
TRAFFIC_TERMS = ["車禍", "交通事故", "肇事", "行車", "駕駛", "與有過失", "過失比例", "車損", "強制險"]
NORMAL_BLOCKERS = ("unreviewed", "judgment_unconfirmed:")


def _citation_family() -> dict[str, str]:
    out = {}
    for f in sorted((SEED / "judgments").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[d["citation"]] = SEED_FAMILY.get(f.stem, "未分類")
    return out


# ---------------------------------------------------------------- 不呼叫 AI 的檢查（預設測試會跑）

@pytest.mark.parametrize("name", CASES)
def test_spike_case_imports_and_is_synthetic(db, name):
    folder = CASES_DIR / name
    meta = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    gold = json.loads((folder / "gold.json").read_text(encoding="utf-8"))
    assert meta["our_side"] == "plaintiff"
    assert 4 <= len(gold["claims"]) <= 7 and all({"summary", "page"} <= set(c) for c in gold["claims"])
    texts = "".join(p.read_text(encoding="utf-8") for p in folder.rglob("*.txt"))
    assert not [n for n in REAL_NAMES[name] if n in texts]
    create_firm("內部測試所")
    case_id, round_id = import_testcase(folder, "內部測試所")
    with admin_session() as s:
        ex = s.scalars(select(Exhibit).where(Exhibit.case_id == case_id)).all()
        assert 3 <= len(ex) <= 5 and {e.prefix for e in ex} == {"原證"}
        assert len(opponent_pages(s, round_id)) == 3
        assert {g["page"] for g in gold["claims"]} == {1, 2, 3}


# ---------------------------------------------------------------- 真的 AI（-m real_ai）

def _gold_table(gold: dict, claims: list[Claim]) -> list[dict]:
    rows = []
    for g in gold["claims"]:
        best = max(claims, key=lambda c: score(g["summary"], f"{c.summary} {c.quote}"), default=None)
        rows.append({"gold": g["summary"], "gold_page": g["page"],
                     "best_claim_seq": best.seq if best else None,
                     "best_claim": best.summary if best else None,
                     "score": round(score(g["summary"], f"{best.summary} {best.quote}"), 3) if best else 0})
    return rows


def _candidates(s, round_id, fam: dict[str, str]) -> dict:
    rows = s.scalars(select(Candidate).where(Candidate.round_id == round_id)).all()
    js, ss, n_ev = [], [], 0
    for c in sorted(rows, key=lambda c: ("EJS".index(c.code[0]), int(c.code[1:]))):
        if c.kind == "evidence":
            n_ev += 1
        elif c.kind == "judgment":
            p = s.get(JudgmentPassage, c.judgment_passage_id)
            j = s.get(Judgment, p.judgment_id)
            js.append({"code": c.code, "citation": j.citation, "family": fam.get(j.citation, "未分類"),
                       "passage_head": p.text[:60]})
        else:
            a = s.get(StatuteArticle, c.statute_article_id)
            ss.append({"code": c.code, "article": article_display(a.statute.name, a.article_no)})
    return {"evidence_pages": n_ev, "judgments": js, "statutes": ss,
            "judgment_family_counts": dict(Counter(j["family"] for j in js))}


def _forced_review_export(s, draft_id, firm_id, member_id) -> dict:
    """模擬律師全部審完、判決全部確認後還剩哪些阻擋；能匯出就讀 Word 標題。結束後 rollback。"""
    pids = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id)).all()
    s.execute(update(Paragraph).where(Paragraph.id.in_(pids)).values(status="reviewed"))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids)).values(lawyer_confirmed=True))
    s.flush()
    after = [{"paragraph_seq": b.paragraph_seq, "reason": b.reason} for b in export_blockers(s, draft_id)]
    title = None
    if not after:
        member = CurrentMember(id=member_id, firm_id=firm_id, role="lawyer", bar_number="(110)北律字第0001號",
                               bar_verified=True)
        data = export_draft(s, draft_id, member)
        title = docx.Document(io.BytesIO(data)).paragraphs[0].text
    s.rollback()
    return {"blockers_after_forced_review": after, "exported_docx_title": title}


def _collect(firm, member, name: str, round_id, fam: dict[str, str]) -> dict:
    gold = json.loads((CASES_DIR / name / "gold.json").read_text(encoding="utf-8"))
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        out = {"family": CASE_FAMILY[name], "status": rnd.status, "error": rnd.error}
        usage = s.execute(select(UsageRecord.step, func.sum(UsageRecord.input_tokens),
                                 func.sum(UsageRecord.output_tokens))
                          .where(UsageRecord.case_id == rnd.case_id).group_by(UsageRecord.step)).all()
        out["usage"] = {step: {"input": int(i or 0), "output": int(o or 0)} for step, i, o in usage}
        claims = list(s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)))
        out["claims"] = [{"seq": c.seq, "summary": c.summary, "quote": c.quote, "quote_verified": c.quote_verified,
                          "selection": c.selection} for c in claims]
        out["gold_recall"] = _gold_table(gold, claims)
        out["candidates"] = _candidates(s, round_id, fam)
        if rnd.status != "drafted":
            return out
        draft = s.scalars(select(Draft).where(Draft.round_id == round_id)).one()
        paragraphs, statuses = [], Counter()
        for p in s.scalars(select(Paragraph).where(Paragraph.draft_id == draft.id).order_by(Paragraph.seq)):
            rendered = render_text(s, p)
            cites = paragraph_citations(s, p.id)
            statuses.update(f"{c.kind}:{c.verify_status}" for c in cites)
            paragraphs.append({
                "seq": p.seq, "heading": p.heading, "text": p.text, "rendered_text": rendered,
                "verify_reasons": p.verify_reasons,
                "citations": [{"code": c.code, "kind": c.kind, "quote": c.quote, "verify_status": c.verify_status,
                               "verify_reasons": c.verify_reasons} for c in cites],
            })
        blockers = [{"paragraph_seq": b.paragraph_seq, "reason": b.reason} for b in export_blockers(s, draft.id)]
        cand_fam = {j["code"]: j for j in out["candidates"]["judgments"]}
        cited_j = sorted({c["code"] for p in paragraphs for c in p["citations"] if c["kind"] == "judgment"})
        whole = "\n".join(p["heading"] + "\n" + p["rendered_text"] for p in paragraphs)
        out.update({
            "draft_id": str(draft.id),
            "paragraphs": paragraphs,
            "citation_status_counts": dict(statuses),
            "blockers": blockers,
            "blockers_other_than_normal": [b for b in blockers if not b["reason"].startswith(NORMAL_BLOCKERS)],
            "selected_judgments": sorted({code for c in claims for code in (c.selection or {}).get("judgments", [])}),
            "cited_judgments": [{"code": code, **{k: cand_fam[code][k] for k in ("citation", "family")}}
                                for code in cited_j if code in cand_fam],
            "traffic_terms_in_draft": {t: whole.count(t) for t in TRAFFIC_TERMS if t in whole},
        })
        out.update(_forced_review_export(s, draft.id, firm, member))
    return out


def _splitter_stats(fam: dict[str, str]) -> dict:
    with admin_session() as s:
        rows = s.execute(select(Judgment.citation, JudgmentPassage.passage_type, func.count(),
                                func.sum(func.length(JudgmentPassage.text)))
                         .join(JudgmentPassage, JudgmentPassage.judgment_id == Judgment.id)
                         .group_by(Judgment.citation, JudgmentPassage.passage_type)).all()
    out: dict[str, dict] = {}
    for citation, kind, n, chars in rows:
        d = out.setdefault(citation, {"family": fam.get(citation, "未分類")})
        d[kind] = {"passages": n, "chars": int(chars or 0)}
    return out


@pytest.mark.real_ai
def test_spike_three_case_types_end_to_end(db):
    with admin_session() as s:
        load_seed(s, SEED)
    fam = _citation_family()
    firm = create_firm(FIRM)
    member = create_member(firm, "lawyer@spike.test")
    rounds = {name: import_testcase(CASES_DIR / name, FIRM) for name in CASES}
    for _, round_id in rounds.values():
        with tenant_session(firm) as s:
            start_round(s, round_id)
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        list(pool.map(lambda n: run_round(firm, rounds[n][1], member, db), CASES))

    report = {"cases": {name: _collect(firm, member, name, rounds[name][1], fam) for name in CASES},
              "splitter": _splitter_stats(fam)}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # spike 只要求流程跑完並產出報告；品質問題寫在報告裡人工判讀，不在這裡擋
    for name in CASES:
        assert report["cases"][name]["status"] == "drafted", (name, report["cases"][name].get("error"))
        assert report["cases"][name]["claims"], name
