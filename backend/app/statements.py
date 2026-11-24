"""聲明區塊：起訴狀的訴之聲明、答辯狀的答辯聲明（設計 5.2、5.3）。

聲明是草稿裡 kind="statement" 的段落，和一般段落一樣要律師審過才能匯出。
訴之聲明的金額只以 {{amount}} 標記存在，由系統依律師確認過的金錢請求項目加總產生，AI 不寫金額。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.case_types import answer_statement, auto_money_statement, complaint_statement
from app.models import Case, CaseParty, Claim, Draft, Paragraph, Round

AMOUNT_MARKER = "{{amount}}"
STATEMENT_LABEL = {"complaint": "訴之聲明", "answer": "答辯聲明"}
BODY_LABEL = {"complaint": "事實及理由", "answer": "答辯理由"}


def confirmed_requests(s: Session, round_id: UUID) -> list[Claim]:
    return list(s.scalars(select(Claim).where(Claim.round_id == round_id, Claim.kind == "request",
                                              Claim.confirmed_by.is_not(None)).order_by(Claim.seq)))


def round_total(s: Session, round_id: UUID) -> int:
    """律師確認過的金錢請求項目加總（訴之聲明與訴訟標的金額的唯一來源）。"""
    return sum(c.amount or 0 for c in confirmed_requests(s, round_id) if c.request_type == "monetary")


def _has_money(s: Session, round_id: UUID) -> bool:
    return any(c.request_type == "monetary" for c in confirmed_requests(s, round_id))


def _parties(s: Session, case_id: UUID) -> list[CaseParty]:
    return list(s.scalars(select(CaseParty).where(CaseParty.case_id == case_id).order_by(CaseParty.seq)))


def auto_money(s: Session, rnd: Round) -> bool:
    return auto_money_statement(_parties(s, rnd.case_id), rnd.joint_liability)


def statement_template(s: Session, rnd: Round) -> str | None:
    case = s.get(Case, rnd.case_id)
    if rnd.brief_kind == "answer":
        return answer_statement(case.case_type)
    if rnd.brief_kind != "complaint":
        return None
    items = confirmed_requests(s, rnd.id)
    return complaint_statement(
        case.case_type, has_money=any(c.request_type == "monetary" for c in items),
        has_non_money=any(c.request_type == "non_monetary" for c in items), joint=rnd.joint_liability,
        provisional=rnd.provisional_execution, auto_money=auto_money(s, rnd), variant=rnd.statement_variant)


def create_statement(s: Session, draft: Draft) -> Paragraph | None:
    """起草結束時建立聲明段落；複製輪次帶過來的文字優先，否則用範本。準備書狀沒有聲明。"""
    rnd = s.get(Round, draft.round_id)
    template = statement_template(s, rnd)
    if template is None:
        return None
    p = Paragraph(firm_id=draft.firm_id, draft_id=draft.id, seq=0, kind="statement", heading="",
                  text=rnd.statement_text or template, status="unreviewed", verify_reasons=[])
    s.add(p)
    s.flush()
    return p


def statement_of(s: Session, draft_id: UUID) -> Paragraph | None:
    return s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id, Paragraph.kind == "statement")).first()


def round_of(s: Session, p: Paragraph) -> Round:
    return s.get(Round, s.get(Draft, p.draft_id).round_id)


def amount_marker_allowed(s: Session, p: Paragraph) -> bool:
    """{{amount}} 只能出現在「有確認金錢項目的起訴狀」的聲明段落。"""
    if p.kind != "statement":
        return False
    rnd = round_of(s, p)
    return rnd.brief_kind == "complaint" and _has_money(s, rnd.id)


def statement_reasons(s: Session, p: Paragraph) -> list[str]:
    rnd = round_of(s, p)
    out: list[str] = []
    text = p.text or ""
    if "○" in text:
        out.append("statement_blank")
    if rnd.brief_kind == "complaint" and _has_money(s, rnd.id):
        if auto_money(s, rnd):
            if AMOUNT_MARKER not in text:
                out.append("statement_amount_missing")
        elif rnd.amounts_checked_by is None:
            out.append("amounts_unchecked")
    return out


def draft_reasons(s: Session, draft_id: UUID) -> list[str]:
    """整份草稿層級的阻擋原因：聲明不見了、起訴狀沒有訴訟標的金額。"""
    rnd = s.get(Round, s.get(Draft, draft_id).round_id)
    out: list[str] = []
    if rnd.brief_kind in STATEMENT_LABEL:
        st = statement_of(s, draft_id)
        if st is None or not (st.text or "").strip():
            out.append("statement_missing")
    if rnd.brief_kind == "complaint" and claim_value(s, rnd)[1] is None:
        out.append("claim_value_missing")
    return out


def claim_value(s: Session, rnd: Round) -> tuple[str, int | None]:
    """只有金錢請求：訴訟標的金額＝律師填的值，否則用加總（加總為 0 視為沒有）。
    有任何非金錢請求：訴訟標的價額，只能由律師填（金錢加總不等於價額）。"""
    items = confirmed_requests(s, rnd.id)
    if items and all(c.request_type == "monetary" for c in items):
        return "訴訟標的金額", rnd.claim_value if rnd.claim_value is not None else (round_total(s, rnd.id) or None)
    return "訴訟標的價額", rnd.claim_value
