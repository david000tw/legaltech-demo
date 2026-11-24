"""卷證說明（設計 5.6）：每份我方卷證的「證明方向」與「注意事項」，每句帶原文出處。

- 只處理「從沒處理過、或原文變了」的卷證（以原文指紋判斷）；律師刪光說明的卷證不會被重新收費處理；
- 掃描頁（還沒有文字層）不送 AI，畫面列「待辨識」；
- 律師改過的句子重新產生時保留；每種說明最多 3 句，扣掉保留的律師句。
"""
from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.ai.schemas import EVIDENCE_NOTES_SCHEMA
from app.amounts import parse_amounts
from app.dates import parse_dates, parse_month_days
from app.db import tenant_session
from app.ingest.service import current_page_text
from app.models import AIJob, Case, Document, EvidenceNote, Page
from app.pipeline.case_context import case_context
from app.pipeline.jobs import JobContext, JobRule
from app.sources import (
    SourcePage, check_source, code_key, evidence_documents, page_chars, pending_ocr, soften, source_view, split_pages,
)

log = logging.getLogger(__name__)
NOTES_BATCH_CHARS = 60_000
MAX_PER_KIND = 3
KINDS = ("proves", "caution")


def document_fingerprint(s: Session, document_id: UUID) -> str:
    """各頁 id 接上目前文字版本（掃描頁寫 ocr）：上傳新版本、校正文字、辨識完成都會改變指紋。"""
    parts = []
    for page in s.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.page_no)):
        pt = current_page_text(s, page.id)
        parts.append(f"{page.id}:{pt.id if pt is not None else 'ocr'}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _has_text(pages: list[SourcePage]) -> bool:
    return any(p.text is not None and p.text.strip() for p in pages)


def documents_needing_notes(s: Session, case_id: UUID) -> list[Document]:
    return [doc for _, doc, pages in evidence_documents(s, case_id)
            if _has_text(pages) and doc.notes_fingerprint != document_fingerprint(s, doc.id)]


NO_TEXT = "還沒有可讀文字的卷證：請等辨識完成，或上傳文字檔"


def ready(s: Session, case_id: UUID) -> None:
    if not any(_has_text(pages) for _, _, pages in evidence_documents(s, case_id)):
        raise JobRule("no_text", NO_TEXT)
    if not documents_needing_notes(s, case_id):
        raise JobRule("no_pending", "所有卷證都已有說明，原文也沒有變更")


def _batches(docs: list[tuple[str, UUID, list[SourcePage]]]) -> list[list[tuple[str, UUID, list[SourcePage]]]]:
    """依卷證順序累加文字長度，超過上限就切下一批；單份超過上限時依頁切成幾段，各段的說明最後合在一起存。"""
    out: list[list] = []
    size = 0
    for code, doc_id, pages in docs:
        parts = [pages] if page_chars(pages) <= NOTES_BATCH_CHARS else split_pages(pages, NOTES_BATCH_CHARS)
        for part in parts:
            n = page_chars(part)
            if not out or (out[-1] and size + n > NOTES_BATCH_CHARS):
                out.append([])
                size = 0
            out[-1].append((code, doc_id, part))
            size += n
    return out


def _merge(items: list[tuple[str, UUID, list[SourcePage]]]) -> list[tuple[str, UUID, list[SourcePage]]]:
    """同一份卷證被切成幾段時，合回一筆（頁依原順序）。"""
    merged: dict[UUID, tuple[str, UUID, list[SourcePage]]] = {}
    for code, doc_id, pages in items:
        if doc_id in merged:
            merged[doc_id][2].extend(pages)
        else:
            merged[doc_id] = (code, doc_id, list(pages))
    return list(merged.values())


def _user(context: str, batch) -> str:
    body = "\n\n".join(f"【{p.code} 第{p.page_no}頁】\n{p.text}"
                       for _, _, pages in batch for p in pages if p.text is not None)
    return f"{context}\n\n【我方卷證】\n{body}\n\n請依規則為每份卷證寫證明方向與注意事項。"


def _save(s: Session, job: AIJob, batch, data: dict, fingerprints: dict[UUID, str]) -> None:
    doc_ids = [doc_id for _, doc_id, _ in batch]
    by_code = {code_key(code): (doc_id, {p.page_no: p for p in pages}) for code, doc_id, pages in batch}
    lawyer: dict[tuple[UUID, str], int] = {}
    for n in s.scalars(select(EvidenceNote).where(EvidenceNote.document_id.in_(doc_ids),
                                                  EvidenceNote.origin == "lawyer")):
        lawyer[(n.document_id, n.kind)] = lawyer.get((n.document_id, n.kind), 0) + 1
    s.execute(delete(EvidenceNote).where(EvidenceNote.document_id.in_(doc_ids), EvidenceNote.origin == "ai"))
    counts = {(doc_id, k): lawyer.get((doc_id, k), 0) for doc_id in doc_ids for k in KINDS}  # 同一份被分成兩組也一起算
    for group in data.get("documents", []) or []:
        entry = by_code.get(code_key(str(group.get("exhibit", ""))))
        if entry is None:
            log.warning("evidence_notes job %s: exhibit %r not in this batch; discarded", job.id, group.get("exhibit"))
            continue
        doc_id, pages = entry
        for seq, n in enumerate(group.get("notes", []) or [], start=1):
            kind, text = n.get("kind"), soften(str(n.get("text", "")).strip())
            if kind not in KINDS or not text or counts[(doc_id, kind)] >= MAX_PER_KIND:
                continue
            page_no = n.get("page_no")
            page = pages.get(page_no) if isinstance(page_no, int) else None
            quote = str(n.get("quote", ""))
            if n.get("absence"):
                status, reasons = "absence", []
            else:
                reasons = check_source(page, quote, dates=parse_dates(text), amounts=parse_amounts(text),
                                       month_days=parse_month_days(text))
                status = "not_found" if reasons else "found"
            s.add(EvidenceNote(firm_id=job.firm_id, case_id=job.case_id, document_id=doc_id, kind=kind, seq=seq,
                               text=text, page_id=page.page_id if page else None, quote=quote, status=status,
                               reasons=reasons, origin="ai"))
            counts[(doc_id, kind)] += 1
    # 有說明（AI 新寫的或保留的律師句）才算處理過；AI 漏寫的卷證維持待處理，可以再按一次（critic #1）。
    # 律師之後刪光說明也不會被重新處理，因為指紋已經記下。
    for doc_id in doc_ids:
        if any(counts[(doc_id, k)] for k in KINDS):
            s.get(Document, doc_id).notes_fingerprint = fingerprints[doc_id]


def generate_evidence_notes(job: AIJob, provider: AIProvider, ctx: JobContext) -> None:
    with tenant_session(job.firm_id) as s:
        case = s.get(Case, job.case_id)
        context = case_context(s, case)
        pending = {d.id for d in documents_needing_notes(s, case.id)}
        docs = [(code, doc.id, pages) for code, doc, pages in evidence_documents(s, case.id) if doc.id in pending]
        fingerprints = {doc_id: document_fingerprint(s, doc_id) for _, doc_id, _ in docs}
    system = load_prompt("evidence_notes")
    batches = _batches(docs)
    items: list = []
    groups: list = []
    for i, batch in enumerate(batches):
        result = ctx.call("evidence_notes", system, _user(context, batch), EVIDENCE_NOTES_SCHEMA, "medium")
        items.extend(batch)
        groups.extend(result.data.get("documents", []) or [])
        if i + 1 < len(batches) and batches[i + 1][0][1] == batch[-1][1]:
            continue  # 同一份卷證的下一段還沒處理：先不寫，存檔時會先刪掉該份的 AI 說明，後一段會蓋掉前一段
        ctx.write(lambda s, items=_merge(items), data={"documents": groups}: _save(s, job, items, data, fingerprints))
        items, groups = [], []


def note_json(s: Session, n: EvidenceNote) -> dict:
    return {"id": str(n.id), "kind": n.kind, "text": n.text, "origin": n.origin,
            "source": source_view(s, n.page_id, n.quote, n.status, n.reasons or [])}


def notes_view(s: Session, case_id: UUID) -> list[dict]:
    pending = {d.id for d in documents_needing_notes(s, case_id)}
    out = []
    for code, doc, pages in evidence_documents(s, case_id):
        notes = s.scalars(select(EvidenceNote).where(EvidenceNote.document_id == doc.id)).all()
        notes = sorted(notes, key=lambda n: (KINDS.index(n.kind), n.seq, n.created_at))
        out.append({"document_id": str(doc.id), "exhibit": code, "filename": doc.filename,
                    "needs_notes": doc.id in pending, "pending_ocr": pending_ocr(pages),
                    "notes": [note_json(s, n) for n in notes]})
    return out
