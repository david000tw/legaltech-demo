"""候選清單（卷證頁、判決法院判斷段、法條）與 AI 對應篩選。

AI 只能從候選清單挑代碼；清單外的代碼與 AI 想引用的其他項目不丟掉，逐筆對回候選或原樣列給律師看
（設計 5.7），並逐字驗證卷證摘錄。
"""
from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIError, AIProvider
from app.ai.usage import call_ai
from app.ai.schemas import select_schema
from app.ai.tokens import estimate_tokens
from app.corpus.pins import pinned_passages
from app.corpus.search import article_display, search_articles, search_page_texts, search_pages, search_passages
from app.exhibit_labels import evidence_label
from app.ingest.service import current_page_text
from app.models import (
    Candidate, Case, Claim, Document, Exhibit, Judgment, JudgmentPassage, Page, PageText, Round,
    StatuteArticle,
)
from app.pipeline.case_context import case_context
from app.pipeline.claims import SIDE_LABEL, claim_filers, selected_claims
from app.text_norm import find_quote, normalize_text, quote_length

EVIDENCE_TOKEN_BUDGET = 150_000
PAGES_PER_CLAIM = 20
PASSAGES_PER_CLAIM = 8
ARTICLES_PER_CLAIM = 5
MIN_QUOTE = 15
SELECT_TOKEN_LIMIT = 180_000  # 送出前估算；超過就明確失敗，不讓模型默默截斷
KIND_PREFIX = {"evidence": "E", "judgment": "J", "statute": "S"}


def _claims(s: Session, round_id: UUID) -> list[Claim]:
    return selected_claims(s, round_id)


def _evidence_pages(s: Session, case_id: UUID) -> list[PageText]:
    """本案卷證中目前有文字的頁（文字層、辨識或手動輸入的目前版本），依原證號、頁碼排序。"""
    pages = s.scalars(
        select(Page).join(Document, Document.id == Page.document_id)
        .join(Exhibit, Exhibit.document_id == Document.id)
        .where(Document.case_id == case_id, Document.kind == "evidence")
        .order_by(Exhibit.prefix, Exhibit.number, Page.page_no)
    ).all()
    return [pt for pt in (current_page_text(s, p.id) for p in pages) if pt is not None]


def _unique(items: list) -> list:
    seen, out = set(), []
    for x in items:
        if x.id not in seen:
            seen.add(x.id)
            out.append(x)
    return out


def build_candidates(s: Session, round_id: UUID) -> list[Candidate]:
    rnd = s.get(Round, round_id)
    claims = _claims(s, round_id)
    queries = [claim_query(c) for c in claims]

    all_pages = _evidence_pages(s, rnd.case_id)
    ours = sum(estimate_tokens(pt.text) for pt in all_pages)
    if ours <= EVIDENCE_TOKEN_BUDGET:
        evidence = all_pages
    else:
        hit_ids = {pt.id for q in queries for pt in search_pages(s, rnd.case_id, q, PAGES_PER_CLAIM)}
        evidence = [pt for pt in all_pages if pt.id in hit_ids]
    # 對方證物頁（對方證物計畫）：我方頁優先，放得下剩下的預算就全收，否則依主張搜尋
    from app.opponent_exhibits import opponent_exhibit_pages, refresh_case_opponent_exhibits
    refresh_case_opponent_exhibits(s, rnd.case_id)
    theirs = opponent_exhibit_pages(s, rnd.case_id, rnd.id)
    left = EVIDENCE_TOKEN_BUDGET - sum(estimate_tokens(pt.text) for pt in evidence)
    if sum(estimate_tokens(pt.text) for pt in theirs) <= left:
        evidence = evidence + theirs
    else:  # 依主張搜尋，依命中順序加入，加到剩下的預算用完為止
        chosen: set = set()
        used = 0
        for q in queries:
            for pt in search_page_texts(theirs, q, PAGES_PER_CLAIM):
                n = estimate_tokens(pt.text)
                if pt.id not in chosen and used + n <= left:
                    chosen.add(pt.id)
                    used += n
        evidence = evidence + [pt for pt in theirs if pt.id in chosen]
    case_type = s.get(Case, rnd.case_id).case_type  # 同類型加權、不排除（設計 5.3）
    # 律師釘選的段落排最前面，一定拿到最前面的 J 代碼（設計 5.9）
    pinned = pinned_passages(s, rnd.case_id)
    pinned_ids = {p.id for p in pinned}
    passages = _unique(pinned + [p for q in queries
                                 for p in search_passages(s, q, PASSAGES_PER_CLAIM, case_type=case_type)])
    articles = _unique([a for q in queries for a in search_articles(s, q, ARTICLES_PER_CLAIM, case_type=case_type)])

    out: list[Candidate] = []
    for i, pt in enumerate(evidence, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"E{i}", kind="evidence",
                             page_text_id=pt.id))
    for i, p in enumerate(passages, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"J{i}", kind="judgment",
                             judgment_passage_id=p.id, pinned=p.id in pinned_ids))
    for i, a in enumerate(articles, start=1):
        out.append(Candidate(firm_id=rnd.firm_id, round_id=round_id, code=f"S{i}", kind="statute",
                             statute_article_id=a.id))
    s.add_all(out)
    s.flush()
    return out


def round_candidates(s: Session, round_id: UUID) -> dict[str, Candidate]:
    rows = s.scalars(select(Candidate).where(Candidate.round_id == round_id)).all()
    return {c.code: c for c in rows}


def candidate_source(s: Session, c: Candidate) -> tuple[str, str]:
    """回傳 (標題, 原文)。"""
    if c.kind == "evidence":
        pt = s.get(PageText, c.page_text_id)
        page = s.get(Page, pt.page_id)
        doc = s.get(Document, page.document_id)
        return f"{evidence_label(s, pt.id)}（{doc.filename}）", pt.text
    if c.kind == "judgment":
        p = s.get(JudgmentPassage, c.judgment_passage_id)
        j = s.get(Judgment, p.judgment_id)
        note = f"【註記：{j.annotation}】" if j.annotation else ""  # 少數見解等，AI 與律師都要看到
        pin = "【律師釘選】" if c.pinned else ""
        return f"{j.citation}（法院判斷段）{note}{pin}", p.text
    a = s.get(StatuteArticle, c.statute_article_id)
    return article_display(a.statute.name, a.article_no), a.text


def format_candidates(s: Session, cands: list[Candidate]) -> str:
    order = sorted(cands, key=lambda c: ("EJS".index(c.code[0]), int(c.code[1:])))
    blocks = []
    for c in order:
        title, text = candidate_source(s, c)
        blocks.append(f"[{c.code}] {title}\n{text}")
    return "\n\n".join(blocks)


def filer_line(filers: dict | None, claim: Claim) -> str:
    """主張的提出者（claims.claim_filers 算好的），接在主張摘要下一行。"""
    return f"\n　提出者：{filers[claim.id]}" if filers and claim.id in filers else ""


def claim_query(c: Claim) -> str:
    """建候選時的搜尋字串：主張＝摘要＋原文；請求項目＝名稱＋請求權基礎＋佐證原文。"""
    if c.kind == "request":
        return " ".join([c.summary, c.basis_text or "", *(e.get("quote", "") for e in c.evidence or [])])
    return f"{c.summary} {c.quote}"


def claim_title(c: Claim) -> str:
    if c.kind == "request":
        what = f"新臺幣{c.amount:,}元" if c.request_type == "monetary" and c.amount is not None else "非金錢請求"
        return f"請求項目{c.seq}：{c.summary}（{what}）"
    return f"主張{c.seq}：{c.summary}"


def claim_source(c: Claim) -> str:
    """主張附原文；請求項目附找得到的佐證原文（找不到的不給 AI，避免被當成事實）。"""
    if c.kind == "request":
        return "".join(f"\n　佐證：「{e['quote']}」（{e.get('exhibit', '')}第{e.get('page_no')}頁）"
                       for e in c.evidence or [] if e.get("found"))
    return f"\n　原文：「{c.quote}」"


def claims_header(brief_kind: str, with_results: bool = False) -> str:
    if brief_kind == "complaint":
        return "【我方請求項目與對應結果】" if with_results else "【我方請求項目】"
    return "【對造主張與對應結果】" if with_results else "【對造主張】"


def format_claims(claims: list[Claim], filers: dict | None = None) -> str:
    return "\n".join(f"{claim_title(c)}{filer_line(filers, c)}{claim_source(c)}" for c in claims)


def _empty_selection() -> dict:
    return {"evidence": [], "judgments": [], "statutes": [], "note": "", "references": [], "unverified_quotes": []}


_ARTICLE = re.compile(r"^(?P<law>.+?)第(?P<no>\d+)條(?:之(?P<sub>\d+))?")  # 之後的第N項、款、前段、但書都不影響對回
_CASE_CORE = re.compile(r"\d+年度?\D{1,10}?字第\d+號")
_FIELD = {"judgment": "judgments", "statute": "statutes"}


def resolve_reference(s: Session, text: str, cands: dict[str, Candidate]) -> dict:
    """把 AI 想引用、但沒有用代碼填的項目對回候選：
    mapped＝唯一對到一筆候選；ambiguous_passage＝對到判決，但該判決在候選中有多個段落；unmatched＝其他。"""
    out = {"text": text, "status": "unmatched", "code": None}
    norm = normalize_text(text or "")
    if not norm:
        return out
    if norm in cands:  # 代碼填錯欄位（例如判決代碼寫進法條欄）
        out.update(status="mapped", code=norm)
        return out
    m = _ARTICLE.match(norm)
    if m:
        want = normalize_text(article_display(m["law"], m["no"] + (f"-{m['sub']}" if m["sub"] else "")))
        hits = [c.code for c in cands.values() if c.kind == "statute" and normalize_text(
            article_display(s.get(StatuteArticle, c.statute_article_id).statute.name,
                            s.get(StatuteArticle, c.statute_article_id).article_no)) == want]
        if len(hits) == 1:
            out.update(status="mapped", code=hits[0])
        return out
    core = _CASE_CORE.search(norm)
    if core is None:
        return out
    by_judgment: dict = {}
    for c in cands.values():
        if c.kind != "judgment":
            continue
        j = s.get(Judgment, s.get(JudgmentPassage, c.judgment_passage_id).judgment_id)
        cit = normalize_text(j.citation)
        court = cit[:cit.find(core.group(0))] if core.group(0) in cit else None
        if court is None:
            continue
        written = norm[:core.start()]
        if "院" in written and court not in written:  # 寫了法院（含「臺中地院」這類簡稱）就必須是完整法院名稱
            continue
        by_judgment.setdefault(j.id, []).append(c.code)
    if len(by_judgment) == 1:
        codes = next(iter(by_judgment.values()))
        if len(codes) == 1:
            out.update(status="mapped", code=codes[0])
        else:
            out.update(status="ambiguous_passage")
    return out


def select_for_claims(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> None:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    claims = _claims(s, round_id)
    cands = round_candidates(s, round_id)
    user = (case_context(s, case, rnd.brief_kind) + "\n\n"
            f"我方立場：{SIDE_LABEL[case.our_side]}。\n\n{claims_header(rnd.brief_kind)}\n"
            f"{format_claims(claims, claim_filers(s, claims))}\n\n"
            f"【候選資料】\n{format_candidates(s, list(cands.values()))}")
    if estimate_tokens(user) > SELECT_TOKEN_LIMIT:
        raise AIError("other", f"對應步驟的輸入過長（約 {estimate_tokens(user):,} token），請減少卷證或拆成多輪")
    codes = {field: sorted((c.code for c in cands.values() if c.kind == kind), key=lambda x: int(x[1:]))
             for field, kind in (("evidence", "evidence"), ("judgments", "judgment"), ("statutes", "statute"))}
    result = call_ai(provider, firm_id=rnd.firm_id, case_id=case.id, member_id=member_id, step="select",
                     system=load_prompt("select"), user=user, schema=select_schema(codes), effort="medium")

    by_seq = {c.seq: c for c in claims}
    chosen: dict[int, dict] = {}
    orphans: list[dict] = []
    for item in result.data.get("selections", []):
        claim = by_seq.get(item.get("claim_seq"))
        if claim is None:  # 主張編號對不到：整筆列給律師看，不悄悄丟掉
            orphans.append(item)
            continue
        sel = chosen.get(claim.seq) or _empty_selection()  # 同一主張被拆成兩筆：合併
        note = str(item.get("note", ""))
        sel["note"] = "；".join(x for x in (sel["note"], note) if x)

        def refer(text: str) -> None:
            """清單外的項目一律列進 references；能唯一對回判決或法條候選的，補進該主張。"""
            for r in resolve_references(s, text, cands):
                sel["references"].append(r)
                field = _FIELD.get(cands[r["code"]].kind) if r["status"] == "mapped" else None
                if field and r["code"] not in sel[field]:
                    sel[field].append(r["code"])

        for ev in item.get("evidence", []):
            code, quote = str(ev.get("code", "")), str(ev.get("quote", ""))
            c = cands.get(code)
            if c is None or c.kind != "evidence":
                refer(code)
                continue
            pt = s.get(PageText, c.page_text_id)
            if quote_length(quote) >= MIN_QUOTE and find_quote(pt.text, quote) is not None:
                sel["evidence"].append({"code": code, "quote": quote})
            else:
                sel["unverified_quotes"].append({"code": code, "quote": quote})
        for field, kind in (("judgments", "judgment"), ("statutes", "statute")):
            for code in item.get(field, []):  # 沒有候選時 schema 不給這個欄位，缺欄位視為空陣列
                c = cands.get(str(code))
                if c is not None and c.kind == kind:
                    if c.code not in sel[field]:
                        sel[field].append(c.code)
                else:
                    refer(str(code))
        for text in item.get("other_references", []):
            refer(str(text))
        chosen[claim.seq] = sel
    if orphans and claims:
        target = chosen.setdefault(claims[0].seq, _empty_selection())
        for item in orphans:
            texts = ([str(ev.get("code", "")) for ev in item.get("evidence", [])]
                     + [str(x) for f in ("judgments", "statutes", "other_references") for x in item.get(f, [])])
            for t in texts:
                target["references"].append({"text": f"【主張{item.get('claim_seq')}（不存在）】{t}",
                                             "status": "unmatched", "code": None})
    for claim in claims:
        claim.selection = chosen.get(claim.seq, _empty_selection())
    s.flush()


_ARTICLE_ANY = re.compile(r"(?P<law>[^\d、,，;；第]*?)第(?P<no>\d+)條(?:之(?P<sub>\d+))?")
_CONNECTORS = "及與和暨或並"


def resolve_references(s: Session, text: str, cands: dict[str, Candidate]) -> list[dict]:
    """一段文字裡有好幾條法條（「民法第184條、第185條」）時逐條對回，沒寫法律名稱的沿用前一條；
    否則整段當一個項目。"""
    norm = normalize_text(text or "")
    found = list(_ARTICLE_ANY.finditer(norm))
    if len(found) <= 1:
        return [resolve_reference(s, text, cands)]
    out, law = [], ""
    for m in found:
        law = m["law"].strip(_CONNECTORS) or law
        item = f"{law}第{m['no']}條" + (f"之{m['sub']}" if m["sub"] else "")
        out.append(resolve_reference(s, item, cands))
    return out
