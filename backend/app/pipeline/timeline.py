"""大事記（設計 5.7）：從我方卷證與對造書狀整理「日期｜事件｜金額｜出處」。

- 對造書狀來的事件標「對造主張」（依 AI 寫的來源代號判斷，頁碼對不到也一樣）；
- 摘錄要逐字在那一頁，事件的日期與金額（欄位與描述裡寫到的）都要在摘錄裡；
- 各批結果收齊後才一次寫入：中途失敗時舊的大事記原封不動；律師新增或修改的事件重新產生後保留。
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.ai.schemas import TIMELINE_SCHEMA
from app.amounts import parse_amounts
from app.dates import PartialDate, dates_with_context, parse_dates, parse_iso, parse_month_days, roc_label
from app.db import tenant_session
from app.models import AIJob, Case, Document, Page, PageText, TimelineEvent
from app.pipeline.case_context import case_context
from app.pipeline.jobs import JobContext, JobRule
from app.ingest.service import current_page_text
from app.text_norm import normalize_text, source_found
from app.sources import (
    MAX_AMOUNT, OPPONENT_PREFIX, SourcePage, check_source, code_key, page_chars, soften, source_pages, source_view,
    split_pages,
)

TIMELINE_BATCH_CHARS = 60_000


def ready(s: Session, case_id: UUID) -> None:
    if not any(p.text is not None and p.text.strip() for p in source_pages(s, case_id).values()):
        raise JobRule("no_text", "卷證與對造書狀都沒有可讀文字")


def _batches(pages: list[SourcePage]) -> list[list[SourcePage]]:
    """同一份文件的頁放同一批；累加超過上限就切下一批，單份超過上限時依頁切成幾段（結果最後才一起寫入）。"""
    docs: dict[UUID, list[SourcePage]] = {}
    for p in pages:
        docs.setdefault(p.document_id, []).append(p)
    out: list[list[SourcePage]] = []
    size = 0
    for doc_pages in docs.values():
        parts = ([doc_pages] if page_chars(doc_pages) <= TIMELINE_BATCH_CHARS
                 else split_pages(doc_pages, TIMELINE_BATCH_CHARS))
        for part in parts:
            n = page_chars(part)
            if not out or (out[-1] and size + n > TIMELINE_BATCH_CHARS):
                out.append([])
                size = 0
            out[-1].extend(part)
            size += n
    return out


def _user(context: str, batch: list[SourcePage]) -> str:
    body = "\n\n".join(f"【{p.code} 第{p.page_no}頁】\n{p.text}" for p in batch)
    return f"{context}\n\n【卷證與對造書狀】\n{body}\n\n請依規則整理大事記。"


def _event_date(pd: PartialDate | None) -> tuple[date | None, str | None]:
    if pd is None:
        return None, None
    return date(pd.year, pd.month, pd.day or 1), ("day" if pd.day else "month")


def _next_seq(s: Session, case_id: UUID) -> int:
    return (s.scalar(select(func.max(TimelineEvent.seq)).where(TimelineEvent.case_id == case_id)) or 0) + 1


def _same_quote(a: str, b: str) -> bool:
    """同一句原文：正規化後一方包含另一方（AI 每次取的摘錄長短可能不同），太短的不算。"""
    a, b = normalize_text(a or ""), normalize_text(b or "")
    return len(a) >= 6 and len(b) >= 6 and (a in b or b in a)


def _save(s: Session, job: AIJob, events: list[dict], pages: dict[tuple[str, int], SourcePage]) -> None:
    s.execute(delete(TimelineEvent).where(TimelineEvent.case_id == job.case_id, TimelineEvent.origin == "ai"))
    s.flush()
    # 律師改過、新增或刪掉（dismissed）的事件：同一頁同一句原文的 AI 事件不再寫入，避免刪掉的又回來、改過的多一筆原版
    handled = [(e.page_id, e.quote) for e in s.scalars(select(TimelineEvent).where(
        TimelineEvent.case_id == job.case_id, TimelineEvent.origin != "ai", TimelineEvent.page_id.is_not(None)))]
    seq = _next_seq(s, job.case_id)
    evidence_codes = {k[0] for k, p in pages.items() if p.kind == "evidence"}
    for e in events:
        description = soften(str(e.get("description", "")).strip())
        if not description:
            continue
        code = str(e.get("source", "")).strip()
        page_no = e.get("page_no")
        page = pages.get((code_key(code), page_no)) if isinstance(page_no, int) else None
        quote = str(e.get("quote", ""))
        if page is not None and any(pid == page.page_id and _same_quote(q, quote) for pid, q in handled):
            continue
        raw_date = str(e.get("date") or "").strip()
        pd = parse_iso(raw_date) if raw_date else None
        raw_amount = e.get("amount")
        valid = isinstance(raw_amount, int) and not isinstance(raw_amount, bool) and 0 <= raw_amount <= MAX_AMOUNT
        amount = raw_amount if valid else None
        dates = ([pd] if pd else []) + parse_dates(description)
        amounts = ([amount] if amount is not None else []) + parse_amounts(description)
        reasons = check_source(page, quote, dates=dates, amounts=amounts, month_days=parse_month_days(description))
        if raw_date and pd is None:
            reasons.append("date_invalid")
        event_date, precision = _event_date(pd)
        # 「對造書狀」開頭＝對造主張（頁碼對不到也一樣）；我方卷證要對得到證物代號，其他代號不標
        key = code_key(code)
        kind = "opponent" if key.startswith(OPPONENT_PREFIX) else ("evidence" if key in evidence_codes else None)
        s.add(TimelineEvent(firm_id=job.firm_id, case_id=job.case_id, seq=seq, event_date=event_date,
                            date_precision=precision, description=description, amount=amount,
                            page_id=page.page_id if page else None, quote=quote, source_kind=kind, origin="ai",
                            status="not_found" if reasons else "found", reasons=reasons))
        seq += 1


def generate_timeline(job: AIJob, provider: AIProvider, ctx: JobContext) -> None:
    with tenant_session(job.firm_id) as s:
        context = case_context(s, s.get(Case, job.case_id))
        pages = source_pages(s, job.case_id)
    readable = [p for p in pages.values() if p.text is not None and p.text.strip()]
    system = load_prompt("timeline")
    events: list[dict] = []
    for batch in _batches(readable):
        result = ctx.call("timeline", system, _user(context, batch), TIMELINE_SCHEMA, "medium")
        events.extend(result.data.get("events", []) or [])
    ctx.write(lambda s: _save(s, job, events, pages))


def _iso(e: TimelineEvent) -> str | None:
    if e.event_date is None:
        return None
    return e.event_date.isoformat() if e.date_precision == "day" else e.event_date.strftime("%Y-%m")


def _label(e: TimelineEvent) -> str | None:
    if e.event_date is None:
        return None
    d = e.event_date
    return roc_label(PartialDate(d.year, d.month, d.day if e.date_precision == "day" else None))


def event_json(s: Session, e: TimelineEvent) -> dict:
    # AI 事件一律有出處標示：沒有頁碼也沒有摘錄就是「未找到原文」（設計 5.7 任何一項沒過就標紅）；律師事件可以沒有出處
    has_source = e.origin == "ai" or e.page_id is not None or bool((e.quote or "").strip())
    status, reasons = e.status, e.reasons or []
    if e.origin != "lawyer" and status == "found":  # 出處頁之後被修正：用目前文字重新確認摘錄還在
        pt = current_page_text(s, e.page_id) if e.page_id else None
        if pt is None or not source_found(pt.text, e.quote or "", min_chars=6):
            status, reasons = "not_found", ["quote_not_found"]
    return {"id": str(e.id), "date": _iso(e), "date_label": _label(e), "description": e.description,
            "amount": e.amount, "source_kind": e.source_kind, "origin": e.origin,
            "source": source_view(s, e.page_id, e.quote, status, reasons) if has_source else None}


def timeline_outdated(s: Session, case_id: UUID) -> bool:
    """最近一次整理完成之後，本案有新上傳的文件、或某頁出現新的文字版本（辨識完成、律師校正）＝建議重新整理。
    沒整理過就沒有基準，回 False。用工作開始時間比較（保守：整理途中的變動也算）。"""
    job = s.scalars(select(AIJob).where(AIJob.case_id == case_id, AIJob.step == "timeline", AIJob.status == "done")
                    .order_by(AIJob.started_at.desc()).limit(1)).first()
    if job is None:
        return False
    # 只看大事記實際讀的那批文件（對造書狀依 sha256 去重）：複製輪次產生的同一份書狀副本不算變動
    doc_ids = list({p.document_id for p in source_pages(s, case_id).values()})
    if not doc_ids:
        return False
    new_doc = select(Document.id).where(Document.id.in_(doc_ids), Document.created_at > job.started_at)
    new_text = (select(PageText.id).join(Page, Page.id == PageText.page_id)
                .where(Page.document_id.in_(doc_ids), PageText.created_at > job.started_at))
    return s.scalar(select(new_doc.exists() | new_text.exists())) is True


UNCOVERED_SOURCES_PER_DATE = 3
UNCOVERED_SNIPPET_RADIUS = 20


def uncovered_dates(s: Session, case_id: UUID) -> dict:
    """卷證與對造書狀的目前文字裡出現、但大事記沒有事件涵蓋的完整日期（純規則，不用 AI；只給律師參考，不自動建立事件）。
    涵蓋：有「日」的事件涵蓋同一天；只到月的事件涵蓋整個月；律師刪掉的 AI 事件（隱藏保留）也算——
    那個日期律師已經看過、判斷不要，不再列成漏列。沒有文字的頁略過。"""
    events = s.scalars(select(TimelineEvent).where(TimelineEvent.case_id == case_id,
                                                   TimelineEvent.event_date.is_not(None))).all()
    days = {e.event_date for e in events if e.date_precision == "day"}
    months = {(e.event_date.year, e.event_date.month) for e in events if e.date_precision == "month"}
    found: dict[PartialDate, list[dict]] = {}
    for page in source_pages(s, case_id).values():  # 與產生大事記讀的是同一批頁
        if page.text is None or not page.text.strip():
            continue
        seen_on_page: set[PartialDate] = set()
        for pd, snippet in dates_with_context(page.text, UNCOVERED_SNIPPET_RADIUS):
            if pd.day is None or pd in seen_on_page:
                continue
            try:
                d = date(pd.year, pd.month, pd.day)
            except ValueError:  # 2月30日這類不存在的日期
                continue
            seen_on_page.add(pd)
            if d in days or (pd.year, pd.month) in months:
                continue
            found.setdefault(pd, []).append({"label": page.code, "page_no": page.page_no, "snippet": snippet,
                                             "page_id": str(page.page_id)})
    dates = [{"date": date(pd.year, pd.month, pd.day).isoformat(), "date_label": roc_label(pd),
              "source_count": len(srcs), "sources": srcs[:UNCOVERED_SOURCES_PER_DATE]}
             for pd, srcs in sorted(found.items())]
    return {"total": len(dates), "dates": dates}


def timeline_view(s: Session, case_id: UUID) -> list[dict]:
    """有日期的依日期升冪、同日依建立順序；沒有日期的放最後。"""
    events = s.scalars(select(TimelineEvent).where(TimelineEvent.case_id == case_id,
                                                   TimelineEvent.origin != "dismissed")).all()
    events = sorted(events, key=lambda e: (e.event_date is None, e.event_date or date.min, e.seq))
    return [event_json(s, e) for e in events]


def lawyer_event(s: Session, case_id: UUID, *, event_date: PartialDate | None, description: str,
                 amount: int | None, page_id: UUID | None, quote: str, source_kind: str | None) -> TimelineEvent:
    d, precision = _event_date(event_date)
    e = TimelineEvent(firm_id=s.get(Case, case_id).firm_id, case_id=case_id, seq=_next_seq(s, case_id),
                      event_date=d, date_precision=precision, description=description, amount=amount,
                      page_id=page_id, quote=quote, source_kind=source_kind, origin="lawyer", status="unchecked",
                      reasons=[])
    s.add(e)
    s.flush()
    return e


def set_date(e: TimelineEvent, value: PartialDate | None) -> None:
    e.event_date, e.date_precision = _event_date(value)
