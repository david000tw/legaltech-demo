"""起草與重寫：只送主張、對應結果與選定資料的原文；產出後立即驗證。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider, record_usage
from app.ai.schemas import DRAFT_SCHEMA, REWRITE_SCHEMA
from app.models import Case, Citation, Claim, Draft, Paragraph, ReviewEvent, Round
from app.numbering import strip_heading_number
from app.pipeline.candidates import (
    claim_source, claim_title, claims_header, filer_line, format_candidates, round_candidates,
)
from app.pipeline.case_context import case_context
from app.pipeline.claims import SIDE_LABEL, claim_filers, selected_claims
from app.statements import create_statement, round_total
from app.verify.citations import MARKER_RE, attach_citations, verify_paragraph


# 起草步驟最後一句指示與系統提示詞，依書狀種類（設計 5.1、5.2）
DRAFT_INSTRUCTION = {
    "preparatory": "請依寫作規則，逐條回應對造主張，撰寫準備書狀段落。",
    "answer": "請依寫作規則，逐條回應原告起訴狀之主張，撰寫答辯狀之答辯理由段落。",
    "complaint": "請依寫作規則，依我方請求項目撰寫起訴狀之事實及理由段落。",
}
DRAFT_PROMPT = {"preparatory": "draft", "answer": "draft", "complaint": "draft_complaint"}


def _selected_codes(claims: list[Claim]) -> list[str]:
    out: list[str] = []
    for c in claims:
        sel = c.selection or {}
        for code in [e["code"] for e in sel.get("evidence", [])] + sel.get("judgments", []) + sel.get("statutes", []):
            if code not in out:
                out.append(code)
    return out


def _claim_lines(claims: list[Claim], filers: dict | None = None) -> str:
    lines = []
    for c in claims:
        sel = c.selection or {}
        ev = "、".join(f"{e['code']}（摘錄：「{e['quote']}」）" for e in sel.get("evidence", [])) or "無"
        lines.append(f"{claim_title(c)}{filer_line(filers, c)}{claim_source(c)}\n　可用卷證：{ev}\n"
                     f"　可用判決：{'、'.join(sel.get('judgments', [])) or '無'}\n"
                     f"　可用法條：{'、'.join(sel.get('statutes', [])) or '無'}\n"
                     f"　說明：{sel.get('note', '')}")
    return "\n".join(lines)


def draft_context(s: Session, round_id: UUID, extra_codes: list[str] | None = None) -> str:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    claims = selected_claims(s, round_id)
    cands = round_candidates(s, round_id)
    codes = [c for c in _selected_codes(claims) + (extra_codes or []) if c in cands]
    codes = list(dict.fromkeys(codes))
    total = (f"合計新臺幣{round_total(s, round_id):,}元（系統計算）\n\n" if rnd.brief_kind == "complaint" else "")
    return (case_context(s, case, rnd.brief_kind) + "\n\n"
            f"我方立場：{SIDE_LABEL[case.our_side]}。\n\n{claims_header(rnd.brief_kind, with_results=True)}\n"
            f"{_claim_lines(claims, claim_filers(s, claims)) or '（無）'}\n\n{total}"
            f"【可引用的資料原文】\n{format_candidates(s, [cands[c] for c in codes]) or '（無）'}")


def _items(data: dict) -> list[tuple[str, str]]:
    return [(str(c.get("code", "")), str(c.get("quote", ""))) for c in data.get("citations", [])]


def draft_round(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None) -> Draft:
    rnd = s.get(Round, round_id)
    user = draft_context(s, round_id) + "\n\n" + DRAFT_INSTRUCTION[rnd.brief_kind]
    result = provider.run(step="draft", system=load_prompt(DRAFT_PROMPT[rnd.brief_kind]), user=user, schema=DRAFT_SCHEMA,
                          effort="high")
    record_usage(s, rnd.firm_id, rnd.case_id, member_id, "draft", result)
    draft = Draft(firm_id=rnd.firm_id, round_id=round_id)
    s.add(draft)
    s.flush()
    for seq, item in enumerate(result.data.get("paragraphs", []), start=1):
        p = Paragraph(firm_id=rnd.firm_id, draft_id=draft.id, seq=seq,
                      heading=strip_heading_number(str(item.get("heading", ""))),
                      text=str(item.get("text", "")), status="unreviewed", verify_reasons=[])
        s.add(p)
        s.flush()
        attach_citations(s, p, _items(item))
        verify_paragraph(s, p)
    statement = create_statement(s, draft)  # 起訴狀、答辯狀的聲明區塊（由系統產生，不經 AI）
    if statement is not None:
        verify_paragraph(s, statement)
    return draft


class ParagraphChanged(Exception):
    """AI 重寫期間段落被別人改過；不覆蓋對方的修改。"""


def reset_review(p: Paragraph) -> None:
    p.status = "unreviewed"
    p.reviewed_by = None
    p.reviewed_bar_number = None
    p.reviewed_at = None


def replace_paragraph(s: Session, p: Paragraph, text: str, items: list[tuple[str, str]]) -> None:
    """替換內文與引用、確認歸零、退回未審、重新驗證。"""
    s.execute(delete(Citation).where(Citation.paragraph_id == p.id))
    p.text = text
    reset_review(p)
    s.flush()
    attach_citations(s, p, items)
    verify_paragraph(s, p)


def rewrite_paragraph(s: Session, paragraph_id: UUID, instruction: str, provider: AIProvider,
                      member_id: UUID | None) -> Paragraph:
    p = s.get(Paragraph, paragraph_id)
    draft = s.get(Draft, p.draft_id)
    rnd = s.get(Round, draft.round_id)
    used = [code for _, code in MARKER_RE.findall(p.text or "")]
    user = (draft_context(s, rnd.id, used) + f"\n\n【要重寫的段落】\n標題：{p.heading}\n內文（含引用標記）：\n{p.text}"
            f"\n\n【律師指示】\n{instruction}")
    result = provider.run(step="rewrite", system=load_prompt("rewrite"), user=user, schema=REWRITE_SCHEMA,
                          effort="high")
    record_usage(s, rnd.firm_id, rnd.case_id, member_id, "rewrite", result)
    before = p.text
    p = s.get(Paragraph, paragraph_id, with_for_update=True, populate_existing=True)  # AI 可能跑了好幾分鐘
    if p is None or p.text != before:
        raise ParagraphChanged("段落在 AI 重寫期間被修改，請重新載入後再要求重寫")
    replace_paragraph(s, p, str(result.data.get("text", "")), _items(result.data))
    s.add(ReviewEvent(firm_id=p.firm_id, paragraph_id=p.id, member_id=member_id, action="rewrite",
                      before_text=before, after_text=p.text))
    s.flush()
    return p
