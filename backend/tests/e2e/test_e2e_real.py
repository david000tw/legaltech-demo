"""子專案甲驗收：用開發者本機真的 Claude Code 跑 12 件合成案件（會用訂閱額度，預設不跑）。

原本計畫 1B 的 9 件準備書狀，加上起訴狀 2 件、答辯狀 1 件。兩段式執行：先拆解，
起訴狀由測試程式代替律師、照標準答案的金額確認請求項目，再起草。
執行：LEGALTECH_TEST_DB=legaltech_test_e2e PYTHONUTF8=1 uv run pytest -m real_ai tests/e2e -s
只跑部分案件：E2E_ONLY=estate-complaint,tc-answer（逗號分隔）。
結果寫到 docs/acceptance/e2e_report_a.json，供人工確認主張召回、判決類型、引用狀態、聲明與匯出狀頭。
"""
import io
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
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
    Candidate, Case, Citation, Claim, Document, Draft, Judgment, JudgmentPassage, Page, Paragraph, Round, StatuteArticle,
    UsageRecord,
)
from app.pipeline.candidates import resolve_references, round_candidates
from app.ingest.service import current_page_text
from app.pipeline.evidence_notes import notes_view
from app.pipeline.health import report_view
from app.pipeline.jobs import JobRule, latest_job, run_job, start_job
from app.pipeline.timeline import timeline_view
from app.pipeline.run import run_draft, run_extract, start_stage
from app.review.service import edit_paragraph
from app.statements import STATEMENT_LABEL, statement_of
from app.render import render_text
from app.text_norm import normalize_text
from app.verify.citations import export_blockers, paragraph_blockers, paragraph_citations
from tests.helpers import create_firm, create_member

pytestmark = pytest.mark.real_ai

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed"
CASES_DIR = BACKEND / "testdata" / "cases"
ALL_CASES = ["demo-lin", "tc-3946", "redteam-1", "spike-affair-1663", "spike-estate-159", "spike-nominee-524",
             "affair-2", "estate-2", "nominee-2", "demo-complaint", "estate-complaint", "tc-answer"]
CASES = [c for c in ALL_CASES if not os.environ.get("E2E_ONLY") or c in os.environ["E2E_ONLY"].split(",")]
NEW_TYPE_CASES = ["spike-affair-1663", "spike-estate-159", "spike-nominee-524", "affair-2", "estate-2", "nominee-2"]
TITLES = {"spike-estate-159": "家事準備書狀", "estate-2": "家事準備書狀", "demo-complaint": "民事起訴狀",
          "estate-complaint": "家事起訴狀", "tc-answer": "民事答辯狀"}
REPORT = BACKEND.parent / "docs" / "acceptance" / "e2e_report_a.json"
FIRM = "內部測試所（E2E 甲）"
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


def _gold_list(gold: dict) -> list[dict]:
    if "items" in gold:  # 起訴狀：標準答案是請求項目
        return [{"summary": i["label"], "page": None} for i in gold["items"]]
    return gold["claims"]


def _gold_table(gold: dict, claims: list[Claim]) -> list[dict]:
    rows = []
    for g in _gold_list(gold):
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


def _simulated_review(s, draft_id, firm_id, member_id, gold: dict) -> dict:
    """模擬律師全部做完：補聲明空格、核對金額、一般確認＋對 warn 引用做「此句為法院判斷」＋逐段審核。
    看剩哪些阻擋；能匯出就讀 Word 第一段、狀頭、聲明與結尾各行。結束後 rollback，不留下來。"""
    member = CurrentMember(id=member_id, firm_id=firm_id, role="lawyer", bar_number=BAR, bar_verified=True)
    st = statement_of(s, draft_id)
    if st is not None and gold.get("statement_fill"):
        text = st.text
        for blank, fill in gold["statement_fill"]:
            text = text.replace(blank, fill)
        edit_paragraph(s, st.id, text, [], member)
    rnd = s.get(Round, s.get(Draft, draft_id).round_id)
    amounts_confirmed = []
    for p in s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id)):
        for r in paragraph_blockers(s, p):
            if r.startswith("amount_unverified:"):
                amounts_confirmed.append({"paragraph_seq": p.seq, "amount": int(r.split(":", 1)[1])})
                p.amounts_confirmed = [*(p.amounts_confirmed or []), int(r.split(":", 1)[1])]
            if r == "amounts_unchecked":
                rnd.amounts_checked_by, rnd.amounts_checked_at = member_id, datetime.now(timezone.utc)
    s.flush()
    pids = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id)).all()
    s.execute(update(Paragraph).where(Paragraph.id.in_(pids)).values(status="reviewed"))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids)).values(lawyer_confirmed=True))
    s.execute(update(Citation).where(Citation.paragraph_id.in_(pids), Citation.verify_status == "warn")
              .values(court_confirmed=True, court_confirmed_by=member_id, court_confirmed_bar_number=BAR))
    s.flush()
    after = [{"paragraph_seq": b.paragraph_seq, "paragraph_no": b.paragraph_no, "reason": b.reason,
              "scope": b.scope} for b in export_blockers(s, draft_id)]
    out = {"blockers_after_simulated_review": after, "brief_title": export_brief_title(s, draft_id),
           "amounts_confirmed_in_review": amounts_confirmed,
           "exported_first_paragraph": None, "exported_caption": None, "exported_statement": None,
           "exported_has_schedule": None}
    if not after:
        doc = docx.Document(io.BytesIO(export_draft(s, draft_id, member)))
        lines = [p.text for p in doc.paragraphs]
        end = next((i for i, x in enumerate(lines) if x.startswith("為") and x.endswith("事：")), len(lines) - 1)
        tail = lines[lines.index("謹狀"):] if "謹狀" in lines else []
        label = STATEMENT_LABEL.get(rnd.brief_kind)
        statement = None
        if label and label in lines:
            i = lines.index(label) + 1
            statement = []
            while i < len(lines) and lines[i][:2] in ("一、", "二、", "三、", "四、", "五、"):
                statement.append(lines[i])
                i += 1
        out.update({"exported_first_paragraph": lines[0], "exported_caption": lines[:end + 1],
                    "exported_closing": tail, "exported_statement": statement,
                    "exported_has_schedule": "附表" in lines and len(doc.tables) == 1,
                    "exported_text": "\n".join(lines)})
    s.rollback()
    return out


# 測試自己的金額解析（不依賴系統的 parse_amounts）：阿拉伯數字、國字（含大寫）、十百千萬億單位與混寫，
# 例：45,000元、28萬1,894元、2萬5千元、參萬元、二百四十萬元、1.5萬元。
_NUMERAL = "0-9,.零〇一二三四五六七八九兩壹貳參肆伍陸柒捌玖十拾百佰千仟萬億"
_AMOUNT = re.compile(rf"([{_NUMERAL}]+)[ \t]*元")
# 讀不懂的寫法（十餘萬元、數千元）只列出來交人工看，不當成金額
_FUZZY_AMOUNT = re.compile(rf"[{_NUMERAL}餘多數幾]+[ \t]*元")
_CN_DIGIT = {**{c: i for i, c in enumerate("零一二三四五六七八九")}, **{c: i for i, c in enumerate("零壹貳參肆伍陸柒捌玖")},
             "〇": 0, "兩": 2}
_SMALL_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG_UNIT = {"萬": 10**4, "億": 10**8}
_TOKEN = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|.")


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def _norm(text: str) -> str:
    """全形轉半形；空白與換行壓成一個空格（不能刪掉，否則「原證1\\n2,000元」會黏成 12,000）。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or ""))


def _to_int(s: str) -> int | None:
    s = s.strip(",.")
    if not any(ch in "0123456789" or ch in _CN_DIGIT for ch in s):
        return None
    total = section = 0
    num = None
    for tok in _TOKEN.findall(s):
        if tok[0] in "0123456789":
            num = float(tok.replace(",", ""))
        elif tok in _CN_DIGIT:
            num = _CN_DIGIT[tok]
        elif tok in _SMALL_UNIT:
            section += (1 if num is None else num) * _SMALL_UNIT[tok]
            num = None
        elif tok in _BIG_UNIT:
            total += (section + (num or 0)) * _BIG_UNIT[tok]
            section, num = 0, None
        else:
            return None
    value = total + section + (num or 0)
    return int(value) if float(value).is_integer() else None


def _simple_amounts(text: str) -> set[int]:
    return {v for m in _AMOUNT.finditer(_norm(text)) if (v := _to_int(m.group(1))) is not None}


def _unparsed_amounts(text: str) -> list[str]:
    out = []
    for m in _FUZZY_AMOUNT.finditer(_norm(text)):
        body = m.group(0)[:-1].strip().lstrip("餘多")  # 「其餘60萬元」的餘在數字前面，不是約略的意思
        if any(ch in "0123456789" or ch in _CN_DIGIT or ch in _SMALL_UNIT or ch in _BIG_UNIT for ch in body) \
                and _to_int(body) is None:
            out.append(m.group(0))
    return out


def _seed_judgment_texts() -> list[str]:
    return [_flat(json.loads(f.read_text(encoding="utf-8"))["full_text"]) for f in (SEED / "judgments").glob("*.json")]


def _checked_text(review: dict, paragraphs: list[dict]) -> str:
    """有匯出就核對匯出的 Word；被擋下沒匯出時，核對畫面上的段落文字，不能因此略過。"""
    return review.get("exported_text") or "\n".join(
        f"{p.get('heading') or ''}\n{p.get('rendered_text') or ''}" for p in paragraphs)


def _independent_amount_check(name: str, gold: dict, review: dict, paragraphs: list[dict]) -> list[int]:
    """書狀裡每個金額，都要對得上標準答案的請求項目、加總、訴訟標的價額、測試資料原文，或模擬律師核對過的金額。
    判決引文只放行「引文本身」那段文字：測試自己到判決全文比對過引文確實是原文，才把那段從書狀裡拿掉再核對。
    回傳對不上的金額（應為空）。"""
    text = _norm(_checked_text(review, paragraphs))
    allowed = {i["amount"] for i in gold.get("items", []) if i.get("amount") is not None}
    allowed.add(sum(i.get("amount") or 0 for i in gold.get("items", [])))
    if gold.get("claim_value"):
        allowed.add(gold["claim_value"])
    for f in (CASES_DIR / name).rglob("*.txt"):
        allowed |= _simple_amounts(f.read_text(encoding="utf-8"))
    allowed |= {a["amount"] for a in review.get("amounts_confirmed_in_review", [])}
    judgments = _seed_judgment_texts()
    for p in paragraphs:
        for c in p["citations"]:
            quote = _norm(c["quote"] or "").strip()
            if c["kind"] == "judgment" and quote and any(_flat(quote) in j for j in judgments):
                text = text.replace(quote, " ")
    return sorted(_simple_amounts(text) - allowed)


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


def _collect(firm, member, name: str, round_id, raw: dict | None, lawyer_actions: dict) -> dict:
    gold = json.loads((CASES_DIR / name / "gold.json").read_text(encoding="utf-8"))
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        case = s.get(Case, rnd.case_id)
        out = {"case_type": case.case_type, "brief_kind": rnd.brief_kind, "status": rnd.status, "error": rnd.error,
               "lawyer_actions": lawyer_actions}
        usage = s.execute(select(UsageRecord.step, func.sum(UsageRecord.input_tokens),
                                 func.sum(UsageRecord.output_tokens))
                          .where(UsageRecord.case_id == case.id).group_by(UsageRecord.step)).all()
        out["usage"] = {step: {"input": int(i or 0), "output": int(o or 0)} for step, i, o in usage}
        claims = list(s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)))
        out["claims"] = [{"seq": c.seq, "kind": c.kind, "summary": c.summary, "quote": c.quote,
                          "quote_verified": c.quote_verified, "request_type": c.request_type, "amount": c.amount,
                          "amount_note": c.amount_note, "evidence": c.evidence, "selection": c.selection}
                         for c in claims]
        out["gold_recall"] = _gold_table(gold, claims)
        out["candidates"] = _candidates(s, round_id, case.case_type)
        out["references"] = _references(s, round_id, claims, raw)
        if rnd.status != "drafted":
            return out
        draft = s.scalars(select(Draft).where(Draft.round_id == round_id)).one()
        st = statement_of(s, draft.id)
        out["statement"] = ({"text": st.text, "rendered_text": render_text(s, st), "verify_reasons": st.verify_reasons}
                            if st is not None else None)
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
        review = _simulated_review(s, draft.id, firm, member, gold)
        review["amounts_not_traceable"] = _independent_amount_check(name, gold, review, paragraphs)
        review["amounts_unparsed"] = _unparsed_amounts(_checked_text(review, paragraphs))  # 交人工看
        out.update(review)
        if gold.get("must_not_cite"):
            out["redteam"] = _redteam(s, draft.id, gold["must_not_cite"], paragraphs)
    return out


def _act_as_lawyer(firm, member, name: str, round_id) -> dict:
    """起訴狀：照標準答案確認請求項目——AI 的項目對上標準答案的就改成標準金額並確認，
    對不上的刪除，標準答案有、AI 漏掉的補上；訴訟標的價額照標準答案填。回傳做了什麼，寫進報告。"""
    gold = json.loads((CASES_DIR / name / "gold.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    actions = {"matched": [], "deleted": [], "added": []}
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        items = list(s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)))
        used = set()
        for g in gold["items"]:
            best = max((c for c in items if c.id not in used), key=lambda c: score(g["label"], c.summary), default=None)
            if best is not None and score(g["label"], best.summary) > 0:
                used.add(best.id)
                actions["matched"].append({"gold": g["label"], "ai": best.summary, "ai_amount": best.amount,
                                           "gold_amount": g.get("amount"), "ai_note": best.amount_note})
                best.request_type, best.amount = g["request_type"], g.get("amount")
                best.confirmed_by, best.confirmed_at = member, now
            else:
                seq = max([c.seq for c in items] + [0]) + 1 + len(actions["added"])
                s.add(Claim(firm_id=firm, round_id=round_id, seq=seq, kind="request", summary=g["label"], quote="",
                            request_type=g["request_type"], amount=g.get("amount"), evidence=[],
                            confirmed_by=member, confirmed_at=now))
                actions["added"].append(g["label"])
        for c in items:
            if c.id not in used:
                actions["deleted"].append({"ai": c.summary, "ai_amount": c.amount})
                s.delete(c)
        if gold.get("claim_value"):
            rnd.claim_value = gold["claim_value"]
    return actions


def _run_case(firm, member, name: str, round_id, recorder, settings) -> dict:
    with tenant_session(firm) as s:
        start_stage(s, round_id, "extract")
    run_extract(firm, round_id, member, settings, provider_factory=recorder.factory)
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        if rnd.status != "extracted":
            return {}
        complaint = rnd.brief_kind == "complaint"
    actions = _act_as_lawyer(firm, member, name, round_id) if complaint else {}  # 其他書狀：全部勾選
    with tenant_session(firm) as s:
        start_stage(s, round_id, "draft")
    run_draft(firm, round_id, member, settings, provider_factory=recorder.factory)
    return actions


def test_twelve_cases_end_to_end(db):
    with admin_session() as s:
        load_seed(s, SEED)
    firm = create_firm(FIRM)
    member = create_member(firm, "lawyer@e2e.test", bar_number=BAR)
    rounds = {name: import_testcase(CASES_DIR / name, FIRM) for name in CASES}
    recorders = {name: _Recorder() for name in CASES}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        actions = dict(zip(CASES, pool.map(
            lambda n: _run_case(firm, member, n, rounds[n][1], recorders[n], db), CASES)))

    report = {name: _collect(firm, member, name, rounds[name][1], recorders[name].select, actions[name])
              for name in CASES}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    _assert_report(report)


def _assert_report(report: dict) -> None:
    """驗收條件；獨立成函式，報告存檔後也能離線重新檢查。"""
    for name in report:
        r = report[name]
        assert r["status"] == "drafted", (name, r.get("error"))
        assert r["claims"], name
        assert r["references"]["dropped"] == [], (name, r["references"]["dropped"])  # AI 提到的每一項都看得到
    for name in [n for n in NEW_TYPE_CASES if n in report]:
        assert report[name]["candidates"]["other_type_ratio"] < OTHER_TYPE_MAX, (name, report[name]["candidates"])
    if "redteam-1" in report:
        rt = report["redteam-1"]["redteam"]
        if rt["model_followed_injection"]:
            assert rt["export_blocked_for_those_paragraphs"], rt  # AI 被騙時匯出應該被擋
    for name, title in TITLES.items():
        if name not in report:
            continue
        assert report[name]["brief_title"] == title, name
        first = report[name]["exported_first_paragraph"]
        assert first in (None, title), (name, first)  # None＝仍有阻擋，原因在報告裡交人工判斷
    for name in report:
        r = report[name]
        # 模擬審完後只允許「引用驗證失敗」這類交律師判斷的阻擋；其他原因代表系統有問題
        others = [b for b in r["blockers_after_simulated_review"] if not b["reason"].startswith("citation_failed")]
        assert others == [], (name, others)
        assert r["amounts_not_traceable"] == [], (name, r["amounts_not_traceable"])  # 測試程式自己重算
    if "demo-complaint" in report:
        st = report["demo-complaint"]["statement"]
        assert "新臺幣405,000元" in st["rendered_text"] and "{{amount}}" in st["text"], st
    if "estate-complaint" in report:
        r = report["estate-complaint"]
        assert "假執行" not in r["statement"]["text"] and "公同共有" in r["statement"]["text"], r["statement"]
        assert r["exported_has_schedule"] in (None, True), r["exported_has_schedule"]


# ───────────────────────── 子專案乙：卷證說明、大事記、健檢（設計 6.2） ─────────────────────────
# 核對函式是測試程式自己寫的，不 import app.sources／app.dates／app.amounts：產品錯了，核對不會跟著錯。

REPORT_B = BACKEND.parent / "docs" / "acceptance" / "e2e_report_b.json"
FIRM_B = "內部測試所（E2E 乙）"
FORBIDDEN_WORDS = ("已證實", "已驗證")
_OWN_CN = {"〇": 0, "○": 0, "◯": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9}
_OWN_CN_CHARS = "〇○◯零一二三四五六七八九十百"
_OWN_CJK_DATE = re.compile(
    rf"(?<![0-9{_OWN_CN_CHARS}])([0-9]{{1,4}}|[{_OWN_CN_CHARS}]{{1,6}})年(?!度)"
    rf"([0-9]{{1,2}}|[一二三四五六七八九十]{{1,3}}|元)月(?:([0-9]{{1,2}}|[一二三四五六七八九十]{{1,3}})日)?")
_OWN_SEP_DATE = re.compile(r"(?<![0-9./\-])([0-9]{4}|[0-9]{2,3})([/.\-])([0-9]{1,2})\2([0-9]{1,2})(?![0-9])")


def _own_found(page_text: str | None, quote: str) -> bool:
    """摘錄（去空白、全形轉半形後）至少 6 字，且整段出現在該頁。掃描頁（沒有文字）一律不算。"""
    if page_text is None:
        return False
    q = _flat(quote)
    return len(q) >= 6 and q in _flat(page_text)


def _own_number(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if "十" in s or "百" in s:
        total = cur = 0
        for ch in s:
            if ch == "百":
                total, cur = total + (cur or 1) * 100, 0
            elif ch == "十":
                total, cur = total + (cur or 1) * 10, 0
            elif ch in _OWN_CN:
                cur = _OWN_CN[ch]
            else:
                return None
        return total + cur
    if all(ch in _OWN_CN for ch in s):
        return int("".join(str(_OWN_CN[ch]) for ch in s))
    return None


def _own_year(y: int) -> int | None:
    if 1 <= y <= 199:
        return y + 1911  # 民國
    return y if 1900 <= y <= 2100 else None


def _own_dates(text: str) -> set[tuple[int, int, int | None]]:
    """民國、西元、國字（含「一一○」「元月」）與 2025/03/09、114.3.9 寫法；只到月的日寫 None。"""
    flat = _flat(text)
    out: set[tuple[int, int, int | None]] = set()
    for m in _OWN_CJK_DATE.finditer(flat):
        y = _own_number(m.group(1))
        mo = 1 if m.group(2) == "元" else _own_number(m.group(2))
        d = _own_number(m.group(3)) if m.group(3) else None
        y = _own_year(y) if y is not None else None
        if y and mo and 1 <= mo <= 12 and (d is None or 1 <= d <= 31):
            out.add((y, mo, d))
    for m in _OWN_SEP_DATE.finditer(flat):
        y, mo, d = _own_year(int(m.group(1))), int(m.group(3)), int(m.group(4))
        if y and 1 <= mo <= 12 and 1 <= d <= 31:
            out.add((y, mo, d))
    return out


def _date_in(d: tuple[int, int, int | None], quote_dates: set) -> bool:
    if d[2] is None:
        return any(q[0] == d[0] and q[1] == d[1] for q in quote_dates)
    return d in quote_dates


# 沒寫年份的月日（「9月25日」「09/25」）：句子寫到的也要在摘錄裡。不去空白（「09/18 19:42」去空白會黏成一串）
_OWN_MD = re.compile(rf"(?<![0-9{_OWN_CN_CHARS}])([0-9]{{1,2}}|[一二三四五六七八九十]{{1,3}})月"
                     rf"([0-9]{{1,2}}|[一二三四五六七八九十]{{1,3}})日")
_OWN_MD_SLASH = re.compile(r"(?<![0-9./:\-])([0-9]{2})/([0-9]{2})(?![0-9/])")


def _own_month_days(text: str) -> set[tuple[int, int]]:
    """沒寫年份的月日；先拿掉有年份的完整日期。"""
    plain = unicodedata.normalize("NFKC", text or "")
    plain = _OWN_SEP_DATE.sub(" ", _OWN_CJK_DATE.sub(" ", plain))
    out: set[tuple[int, int]] = set()
    for rx in (_OWN_MD, _OWN_MD_SLASH):
        for m in rx.finditer(plain):
            mo, d = _own_number(m.group(1)), _own_number(m.group(2))
            if mo and d and 1 <= mo <= 12 and 1 <= d <= 31:
                out.add((mo, d))
    return out


def _month_day_in(md: tuple[int, int], quote: str) -> bool:
    return md in _own_month_days(quote) or any((q[1], q[2]) == md for q in _own_dates(quote))


def _iso_date(value: str) -> tuple[int, int, int | None]:
    parts = [int(x) for x in value.split("-")]
    return parts[0], parts[1], parts[2] if len(parts) > 2 else None


def _assert_report_b(report: dict) -> None:
    """子專案乙驗收條件（設計 6.2）；報告存檔後也能離線重新檢查。"""
    for name, r in report.items():
        assert r["round_status"] == "drafted", (name, r["round_status"])
        for step, job in r["jobs"].items():
            assert job and job["status"] == "done", (name, step, job)
        pages = r["pages"]

        def found_ok(src: dict, where: str) -> dict:
            page = pages.get(src.get("page_id") or "")
            assert page is not None and _own_found(page["text"], src["quote"]), (name, where, src)
            return page

        texts: list[str] = []
        found_notes = 0
        for n in r["notes"]:
            if n["origin"] != "ai":
                continue
            texts.append(n["text"])
            src = n["source"]
            if src["status"] != "found":
                continue
            found_ok(src, "note")
            quote_dates, quote_amounts = _own_dates(src["quote"]), _simple_amounts(src["quote"])
            for d in _own_dates(n["text"]):
                assert _date_in(d, quote_dates), (name, "note date not in quote", d, n)
            for a in _simple_amounts(n["text"]):
                assert a in quote_amounts, (name, "note amount not in quote", a, n)
            for md in _own_month_days(n["text"]):
                assert _month_day_in(md, src["quote"]), (name, "note month-day not in quote", md, n)
            found_notes += 1
        assert found_notes >= 1, (name, "沒有任何找到原文的卷證說明")

        dated_found = 0
        for e in r["events"]:
            if e["origin"] != "ai":
                continue
            texts.append(e["description"])
            src = e["source"]
            if src and pages.get(src.get("page_id") or "", {}).get("kind") == "opponent":
                assert e["source_kind"] == "opponent", (name, "對造書狀的事件沒標對造主張", e)
            if src is None or src["status"] != "found":
                continue
            found_ok(src, "event")
            dates = set(_own_dates(e["description"])) | ({_iso_date(e["date"])} if e["date"] else set())
            quote_dates = _own_dates(src["quote"])
            for d in dates:
                assert _date_in(d, quote_dates), (name, "event date not in quote", d, e)
            amounts = _simple_amounts(e["description"]) | ({e["amount"]} if e["amount"] is not None else set())
            quote_amounts = _simple_amounts(src["quote"])
            for a in amounts:
                assert a in quote_amounts, (name, "event amount not in quote", a, e)
            for md in _own_month_days(e["description"]):
                assert _month_day_in(md, src["quote"]), (name, "event month-day not in quote", md, e)
            dated_found += bool(e["date"])
        assert dated_found >= 1, (name, "沒有沒標紅且有日期的事件")

        h = r["health"]
        assert h is not None, (name, "沒有健檢結果")
        for it in h["items"]:
            texts += [it["title"], it["explanation"]]
            for b in it["basis"]:
                if b["status"] == "found":
                    found_ok(b, "health basis")
            for ref in it["references"]:
                assert ref["code"] in r["candidate_codes"], (name, "健檢引用不在本輪候選", ref)
        texts += h["issues"]
        if r["brief_kind"] == "complaint":
            assert h["undisputed"] is None, (name, "起訴狀不應有不爭執事項")
        else:
            assert h["undisputed"] is not None, name
            for u in h["undisputed"]:
                texts.append(u["fact"])
                if u["basis"]["status"] == "found":
                    assert found_ok(u["basis"], "undisputed")["kind"] == "opponent", (name, "不爭執事項不是對造書狀", u)
        for text in texts:
            assert not any(w in text for w in FORBIDDEN_WORDS), (name, "出現已證實／已驗證", text)


def _run_job_b(firm, member, case_id, round_id, step, settings) -> str | None:
    """開始並執行一個 AI 工作；前提不符時回傳原因（寫進報告），不丟例外。"""
    try:
        with tenant_session(firm) as s:
            job_id = start_job(s, case_id, round_id, step, member).id
    except JobRule as e:
        return f"{e.code}: {e.message}"
    run_job(firm, job_id, settings)
    return None


def _run_case_b(firm, member, name: str, case_id, round_id, settings) -> dict:
    _run_case(firm, member, name, round_id, _Recorder(), settings)
    rules = {step: _run_job_b(firm, member, case_id, None, step, settings) for step in ("evidence_notes", "timeline")}
    rules["health"] = _run_job_b(firm, member, case_id, round_id, "health", settings)
    return rules


def _job_view(s, case_id, round_id, step, rule: str | None) -> dict | None:
    if rule:
        return {"status": "rule", "error": rule}
    job = latest_job(s, case_id, round_id, step)
    return {"status": job.status, "error": job.error} if job else None


def _stats_b(r: dict) -> dict:
    events = r["events"]
    red = [e for e in events if e["source"] and e["source"]["status"] == "not_found"]
    items = (r["health"] or {}).get("items", [])
    return {
        "notes": dict(Counter(n["source"]["status"] for n in r["notes"])),
        "events": dict(Counter(e["source"]["status"] if e["source"] else "none" for e in events)),
        "events_opponent": sum(e["source_kind"] == "opponent" for e in events),
        "red_ratio": round(len(red) / len(events), 3) if events else None,
        "red_reasons": dict(Counter(x for e in red for x in e["source"]["reasons"])),
        "health_levels": dict(Counter(i["level"] for i in items)),
        "health_basis": dict(Counter(b["status"] for i in items for b in i["basis"])),
        "health_absence": sum(i["absence"] for i in items),
        "undisputed": dict(Counter(u["basis"]["status"] for u in (r["health"] or {}).get("undisputed") or [])),
        "not_in_list": sum(len(i["not_in_list"]) for i in items),
    }


def _collect_b(firm, name: str, case_id, round_id, rules: dict) -> dict:
    with tenant_session(firm) as s:
        rnd = s.get(Round, round_id)
        pages = {}
        for page, doc in s.execute(select(Page, Document).join(Document, Document.id == Page.document_id)
                                   .where(Document.case_id == case_id)).all():
            pt = current_page_text(s, page.id)
            pages[str(page.id)] = {"kind": "evidence" if doc.kind == "evidence" else "opponent",
                                   "text": pt.text if pt is not None else None}
        usage = {}
        tokens_in = UsageRecord.input_tokens + UsageRecord.cache_read_tokens + UsageRecord.cache_write_tokens
        for step, n, inp, out in s.execute(
                select(UsageRecord.step, func.count(), func.sum(tokens_in), func.sum(UsageRecord.output_tokens))
                .where(UsageRecord.case_id == case_id).group_by(UsageRecord.step)).all():
            usage[step] = {"calls": n, "input_tokens": int(inp or 0), "output_tokens": int(out or 0)}
        r = {
            "brief_kind": rnd.brief_kind, "round_status": rnd.status,
            "jobs": {"evidence_notes": _job_view(s, case_id, None, "evidence_notes", rules.get("evidence_notes")),
                     "timeline": _job_view(s, case_id, None, "timeline", rules.get("timeline")),
                     "health": _job_view(s, case_id, round_id, "health", rules.get("health"))},
            "pages": pages,
            "notes": [{"exhibit": d["exhibit"], **n} for d in notes_view(s, case_id) for n in d["notes"]],
            "events": timeline_view(s, case_id),
            "health": report_view(s, round_id),
            "candidate_codes": sorted(round_candidates(s, round_id)),
            "usage": usage,
        }
    r["stats"] = _stats_b(r)
    return r


def test_twelve_cases_evidence_timeline_health(db):
    """子專案乙驗收（設計 6.2）：12 件各跑兩段起草，再跑卷證說明、大事記、健檢，用測試自己的核對檢查。"""
    with admin_session() as s:
        load_seed(s, SEED)
    firm = create_firm(FIRM_B)
    member = create_member(firm, "lawyer-b@e2e.test", bar_number=BAR)
    cases = {name: import_testcase(CASES_DIR / name, FIRM_B) for name in CASES}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rules = dict(zip(CASES, pool.map(lambda n: _run_case_b(firm, member, n, *cases[n], db), CASES)))
    report = {name: _collect_b(firm, name, *cases[name], rules[name]) for name in CASES}
    REPORT_B.parent.mkdir(parents=True, exist_ok=True)
    REPORT_B.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    _assert_report_b(report)
