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
from app.dates import PartialDate, parse_dates, parse_iso, parse_month_days, roc_label
from app.db import tenant_session
from app.models import AIJob, Case, TimelineEvent
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
