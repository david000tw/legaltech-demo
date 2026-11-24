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
    Candidate, Case, Citation, Claim, Draft, Judgment, JudgmentPassage, Paragraph, Round, StatuteArticle, UsageRecord,
)
from app.pipeline.candidates import resolve_references, round_candidates
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
