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
from app.models import Case, CaseParty, Claim, Document, Page, PageText, Round
from app.text_norm import find_quote, quote_length

MIN_QUOTE = 15
from app.case_types import ALIGNMENT_LABEL, SIDE_LABEL  # noqa: E402,F401  沿用舊的匯入位置
from app.pipeline.case_context import case_context  # noqa: E402


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


def _filers(s: Session, round_id: UUID) -> dict:
    """每份對造書狀的遞狀人標示（有選遞狀人才標），放在該書狀每頁開頭。"""
    out = {}
    for d in s.scalars(select(Document).where(Document.round_id == round_id, Document.kind == "opponent_brief")):
        if d.filed_by_party_id:
            party = s.get(CaseParty, d.filed_by_party_id)
            if party is not None:
                out[d.id] = f"【遞狀人：{party.role} {party.name}（{ALIGNMENT_LABEL[party.alignment]}）】\n"
    return out


UNCONFIRMED_FILER = "未確認（原文對不上頁面，請律師確認是誰的主張）"


def _filer_label(party: CaseParty) -> str:
    note = "立場與我方相同，不得反駁" if party.alignment == "aligned" else ALIGNMENT_LABEL[party.alignment]
    return f"{party.role} {party.name}（{note}）"


def selected_claims(s: Session, round_id: UUID) -> list[Claim]:
    """律師勾選「回應」的主張（或請求項目），依順序；第二段的建候選、挑判決、起草只處理這些（設計 5.4）。"""
    return list(s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.selected.is_(True))
                          .order_by(Claim.seq)))


def claim_filers(s: Session, claims: list[Claim]) -> dict[UUID, str]:
    """每條主張的提出者，由系統帶入對應與起草步驟，不靠 AI 在摘要裡寫名字；立場與我方相同者標明不得反駁（設計 5.2）。
    - 本輪對造書狀都是同一位遞狀人：每條主張都是他說的。
    - 否則以主張所在頁 → 書狀 → 遞狀人判定，但只信原文逐字對得上該頁的主張（頁碼是 AI 報的，可能報錯）；
      對不上的標「未確認」（最終審查 critic）。沒選遞狀人的書狀不標。"""
    if not claims:
        return {}
    rnd = s.get(Round, claims[0].round_id)
    docs = s.scalars(select(Document).where(Document.round_id == rnd.id, Document.kind == "opponent_brief")).all()
    parties = {d.id: s.get(CaseParty, d.filed_by_party_id) for d in docs if d.filed_by_party_id}
    parties = {doc_id: p for doc_id, p in parties.items() if p is not None and p.case_id == rnd.case_id}
    if not parties:
        return {}
    only = {p.id: p for p in parties.values()}
    if len(only) == 1 and len(parties) == len(docs):
        label = _filer_label(next(iter(only.values())))
        return {c.id: label for c in claims}
    out: dict[UUID, str] = {}
    for c in claims:
        page = s.get(Page, c.page_id) if c.page_id else None
        party = parties.get(page.document_id) if page else None
        if c.quote_verified and party is not None:
            out[c.id] = _filer_label(party)
        elif not (c.quote_verified and page is not None and page.document_id not in parties):
            out[c.id] = UNCONFIRMED_FILER
    return out


def format_pages(pages: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"【第{n}頁】\n{text}" for n, text in pages)


def extract_claims(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> list[Claim]:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    pages = [op for op in opponent_pages(s, round_id) if op.text is not None]
    filers = _filers(s, round_id)
    user = (case_context(s, case, rnd.brief_kind) + "\n\n"
            f"我方立場：{SIDE_LABEL[case.our_side]}。以下是對造書狀全文，請拆成主張清單。\n\n"
            + format_pages([(op.page_no, filers.get(op.page.document_id, "") + op.text.text) for op in pages]))
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
