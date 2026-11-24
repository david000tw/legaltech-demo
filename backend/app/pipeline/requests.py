"""起訴狀第一段「拆解」：AI 依案情說明與我方卷證提出請求項目，系統比對佐證原文與金額（設計 5.2）。

請求項目存在 claims 表（kind="request"），之後的建候選、挑判決、起草沿用主張的流程。
金額由律師逐項確認；訴之聲明的金額由系統加總（app/statements.py）。
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.ai.usage import call_ai
from app.ai.schemas import REQUESTS_SCHEMA
from app.amounts import parse_amounts
from app.ingest.service import current_page_text
from app.models import Case, Claim, Document, Exhibit, Page, PageText, Round
from app.pipeline.case_context import case_context
from app.text_norm import normalize_text, source_found


@dataclass(frozen=True)
class EvidencePage:
    exhibit: str  # 例：原證1
    page: Page
    text: PageText | None  # None＝還沒有文字（待辨識、辨識失敗）


def evidence_pages(s: Session, case_id: UUID) -> list[EvidencePage]:
    """本案我方卷證的每一頁（含還沒有文字的頁），依原證號、頁碼排序。"""
    rows = s.execute(
        select(Page, Exhibit).join(Document, Document.id == Page.document_id)
        .join(Exhibit, Exhibit.document_id == Document.id)
        .where(Document.case_id == case_id, Document.kind == "evidence")
        .order_by(Exhibit.prefix, Exhibit.number, Page.page_no)
    ).all()
    return [EvidencePage(f"{ex.prefix}{ex.number}", p, current_page_text(s, p.id)) for p, ex in rows]


def pending_ocr_pages(s: Session, case_id: UUID) -> list[str]:
    """要辨識、但還沒有文字，所以沒送給 AI 的頁。"""
    return [f"{ep.exhibit} 第{ep.page.page_no}頁" for ep in evidence_pages(s, case_id)
            if ep.text is None and ep.page.ocr_state != "none"]


def amount_note(request_type: str, amount: int | None, no_receipt: bool, evidence: list[dict]) -> str | None:
    if request_type != "monetary":
        return None
    if no_receipt:
        return "no_receipt"
    if amount is None:
        return "amount_missing"
    found_amounts = {a for e in evidence if e.get("found") for a in parse_amounts(e.get("quote", ""))}
    return None if amount in found_amounts else "amount_not_in_evidence"


def _key(exhibit: str, page_no) -> tuple[str, int] | None:
    try:
        return normalize_text(str(exhibit)), int(page_no)
    except (TypeError, ValueError):
        return None


def extract_requests(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> list[Claim]:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    pages = [ep for ep in evidence_pages(s, case.id) if ep.text is not None]
    body = "\n\n".join(f"【{ep.exhibit} 第{ep.page.page_no}頁】\n{ep.text.text}" for ep in pages)
    user = (case_context(s, case, rnd.brief_kind) + "\n\n【案情說明】\n" + (rnd.narrative or "") +
            "\n\n【我方卷證】\n" + body + "\n\n請依規則整理起訴狀要請求的項目。")
    result = call_ai(provider, firm_id=rnd.firm_id, case_id=case.id, member_id=member_id, step="requests",
                     system=load_prompt("requests"), user=user, schema=REQUESTS_SCHEMA, effort="medium")
    by_key = {(normalize_text(ep.exhibit), ep.page.page_no): ep for ep in pages}
    claims: list[Claim] = []
    for seq, item in enumerate(result.data.get("items", []), start=1):
        evidence = []
        for e in item.get("evidence", []) or []:
            ep = by_key.get(_key(e.get("exhibit", ""), e.get("page_no")))
            quote = str(e.get("quote", ""))
            evidence.append({"page_id": str(ep.page.id) if ep else None, "exhibit": str(e.get("exhibit", "")),
                             "page_no": e.get("page_no"), "quote": quote,
                             "found": bool(ep) and source_found(ep.text.text, quote)})
        rtype = item.get("request_type") if item.get("request_type") in ("monetary", "non_monetary") else "monetary"
        raw = item.get("amount")
        amount = raw if rtype == "monetary" and isinstance(raw, int) and raw >= 0 else None
        first = next((e for e in evidence if e["found"]), None)
        c = Claim(firm_id=rnd.firm_id, round_id=round_id, seq=seq, kind="request",
                  summary=str(item.get("label", "")).strip() or f"請求項目{seq}",
                  quote=first["quote"] if first else "", page_id=UUID(first["page_id"]) if first else None,
                  quote_verified=first is not None, request_type=rtype, amount=amount,
                  basis_text=str(item.get("basis_text", "")) or None, evidence=evidence,
                  amount_note=amount_note(rtype, amount, bool(item.get("no_receipt")), evidence))
        s.add(c)
        claims.append(c)
    s.flush()
    return claims
