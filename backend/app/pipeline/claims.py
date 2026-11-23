"""拆解對造主張：AI 拆出主張與原文摘錄，系統逐字比對摘錄是否真的出自該頁。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider, record_usage
from app.ai.schemas import CLAIMS_SCHEMA
from app.ingest.service import current_page_text
from app.models import Case, Claim, Document, Page, PageText, Round
from app.text_norm import find_quote, quote_length

MIN_QUOTE = 15
SIDE_LABEL = {"plaintiff": "原告", "defendant": "被告"}


@dataclass(frozen=True)
class OpponentPage:
    page_no: int  # 本輪所有對造書狀連續編號
    page: Page
    text: PageText | None


def opponent_pages(s: Session, round_id: UUID) -> list[OpponentPage]:
    docs = s.scalars(select(Document).where(Document.round_id == round_id, Document.kind == "opponent_brief")
                     .order_by(Document.created_at, Document.filename)).all()
    out: list[OpponentPage] = []
    n = 0
    for d in docs:
        for p in s.scalars(select(Page).where(Page.document_id == d.id).order_by(Page.page_no)):
            n += 1
            out.append(OpponentPage(n, p, current_page_text(s, p.id)))
    return out


def format_pages(pages: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"【第{n}頁】\n{text}" for n, text in pages)


def extract_claims(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> list[Claim]:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    pages = [op for op in opponent_pages(s, round_id) if op.text is not None]
    user = (f"我方立場：{SIDE_LABEL[case.our_side]}。以下是對造書狀全文，請拆成主張清單。\n\n"
            + format_pages([(op.page_no, op.text.text) for op in pages]))
    result = provider.run(step="claims", system=load_prompt("claims"), user=user, schema=CLAIMS_SCHEMA,
                          effort="medium")
    record_usage(s, rnd.firm_id, case.id, member_id, "claims", result)
    by_no = {op.page_no: op for op in pages}
    claims: list[Claim] = []
    for seq, item in enumerate(result.data.get("claims", []), start=1):
        quote = str(item.get("quote", ""))
        op = by_no.get(item.get("page_no"))
        verified = (op is not None and quote_length(quote) >= MIN_QUOTE
                    and find_quote(op.text.text, quote) is not None)
        c = Claim(firm_id=rnd.firm_id, round_id=round_id, seq=seq, summary=str(item.get("summary", "")),
                  quote=quote, page_id=op.page.id if op else None, quote_verified=verified)
        s.add(c)
        claims.append(c)
    s.flush()
    return claims
