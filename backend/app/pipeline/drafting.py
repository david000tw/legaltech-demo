"""起草與重寫：只送主張、對應結果與選定資料的原文；產出後立即驗證。"""
from __future__ import annotations

import logging

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIProvider
from app.ai.usage import call_ai
from app.ai.schemas import DRAFT_SCHEMA, REWRITE_SCHEMA
from app.models import Case, Citation, Claim, Draft, Paragraph, ReviewEvent, Round
from app.numbering import strip_heading_number

log = logging.getLogger(__name__)
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


def draft_context(s: Session, round_id: UUID, extra_codes: list[str] | None = None, *,
                  health_job_id: UUID | None = None) -> str:
    rnd = s.get(Round, round_id)
    case = s.get(Case, rnd.case_id)
    claims = selected_claims(s, round_id)
    cands = round_candidates(s, round_id)
    codes = [c for c in _selected_codes(claims) + (extra_codes or []) if c in cands]
    codes = list(dict.fromkeys(codes))
    total = (f"合計新臺幣{round_total(s, round_id):,}元（系統計算）\n\n" if rnd.brief_kind == "complaint" else "")
    base = (case_context(s, case, rnd.brief_kind) + "\n\n"
            f"我方立場：{SIDE_LABEL[case.our_side]}。\n\n{claims_header(rnd.brief_kind, with_results=True)}\n"
            f"{_claim_lines(claims, claim_filers(s, claims)) or '（無）'}\n\n{total}"
            f"【可引用的資料原文】\n{format_candidates(s, [cands[c] for c in codes]) or '（無）'}")
    # 起草的參考（設計 6.3）：只放通過規則檢查的健檢提醒與卷證說明，加上律師的答辯策略；空的段不放
    from app.pipeline.evidence_notes import evidence_notes_for_draft
    from app.pipeline.health import health_for_draft
    extras = [health_for_draft(s, round_id, health_job_id), evidence_notes_for_draft(s, case.id),
              f"【律師答辯策略】\n{rnd.strategy.strip()}" if (rnd.strategy or "").strip() else ""]
    return "\n\n".join([base] + [x for x in extras if x])


def _answered_seqs(s: Session, rnd: Round) -> set[int]:
    """段落可以標註的主張編號：答辯／準備書狀是已勾選的主張，起訴狀是已確認的請求項目。"""
    from app.statements import confirmed_requests
    claims = confirmed_requests(s, rnd.id) if rnd.brief_kind == "complaint" else selected_claims(s, rnd.id)
    return {c.seq for c in claims}


def _items(data: dict) -> list[tuple[str, str]]:
    return [(str(c.get("code", "")), str(c.get("quote", ""))) for c in data.get("citations", [])]


def draft_round(s: Session, round_id: UUID, provider: AIProvider, member_id: UUID | None, *,
                health_job_id: UUID | None = None) -> Draft:
    rnd = s.get(Round, round_id)
    user = draft_context(s, round_id, health_job_id=health_job_id) + "\n\n" + DRAFT_INSTRUCTION[rnd.brief_kind]
    valid_seqs = _answered_seqs(s, rnd)
    result = call_ai(provider, firm_id=rnd.firm_id, case_id=rnd.case_id, member_id=member_id, step="draft",
                     system=load_prompt(DRAFT_PROMPT[rnd.brief_kind]), user=user, schema=DRAFT_SCHEMA, effort="high")
    draft = Draft(firm_id=rnd.firm_id, round_id=round_id)
    s.add(draft)
    s.flush()
    for seq, item in enumerate(result.data.get("paragraphs", []), start=1):
        raw = [x for x in item.get("claim_seqs", []) or [] if isinstance(x, int)]
        kept = sorted(set(x for x in raw if x in valid_seqs))  # 規則檢查：只留本輪有效的主張編號（設計 7）
        if len(kept) != len(set(raw)):
            log.info("draft paragraph %s: dropped claim_seqs %s", seq, sorted(set(raw) - set(kept)))
        p = Paragraph(firm_id=rnd.firm_id, draft_id=draft.id, seq=seq,
                      heading=strip_heading_number(str(item.get("heading", ""))),
                      text=str(item.get("text", "")), status="unreviewed", verify_reasons=[], claim_seqs=kept)
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
    # 內容或來源變了：「審核待完成」作廢（律師沒看過新的版本，設計 5.2）
    p.review_request_by = None
    p.review_request_version = None
    p.review_note = None


def replace_paragraph(s: Session, p: Paragraph, text: str, items: list[tuple[str, str]]) -> None:
    """替換內文與引用、確認歸零、退回未審、重新驗證。"""
    s.execute(delete(Citation).where(Citation.paragraph_id == p.id))
    p.text = text
    p.amounts_confirmed = []  # 內文改過，律師核對過的金額要重新核對（設計 5.5）
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
    result = call_ai(provider, firm_id=rnd.firm_id, case_id=rnd.case_id, member_id=member_id, step="rewrite",
                     system=load_prompt("rewrite"), user=user, schema=REWRITE_SCHEMA, effort="high")
    before = p.text
    p = s.get(Paragraph, paragraph_id, with_for_update=True, populate_existing=True)  # AI 可能跑了好幾分鐘
    if p is None or p.text != before:
        raise ParagraphChanged("段落在 AI 重寫期間被修改，請重新載入後再要求重寫")
    replace_paragraph(s, p, str(result.data.get("text", "")), _items(result.data))
    s.add(ReviewEvent(firm_id=p.firm_id, paragraph_id=p.id, member_id=member_id, action="rewrite",
                      before_text=before, after_text=p.text))
    s.flush()
    return p
