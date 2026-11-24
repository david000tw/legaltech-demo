"""匯出 Word：只有全部段落已審、全部引用通過驗證且判決逐筆確認後才能匯出。"""
from __future__ import annotations

import hashlib
import io
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.case_types import SIDE_LABEL, brief_title, default_addressee, procedure_for
from app.models import Case, CaseParty, Citation, Document, Draft, Exhibit, ExportRecord, Page, PageText, Paragraph, Round
from app.numbering import display_heading
from app.render import render_text
from app.statements import BODY_LABEL, STATEMENT_LABEL, claim_value, statement_of
from app.verify.citations import INVALID_XML_CHARS, Blocker, body_paragraphs, export_blockers, is_removed

FONT = "標楷體"
TAIPEI = ZoneInfo("Asia/Taipei")


class ExportBlocked(Exception):
    def __init__(self, blockers: list[Blocker]):
        self.blockers = blockers
        super().__init__(f"{len(blockers)} 個問題尚未解決")


def _setup(d: docx.Document) -> None:
    for sec in d.sections:
        sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(2.5)
    normal = d.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(14)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    pf = normal.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(24)
    pf.space_before = pf.space_after = Pt(0)  # 預設範本有段後距，會讓行距不是固定 24pt


def roc_date(d: date) -> str:
    """民國紀年：date(2026, 11, 23) → 中華民國115年11月23日。"""
    return f"中華民國{d.year - 1911}年{d.month}月{d.day}日"


def _clean(v: str | None) -> str:
    """狀頭欄位來自律師輸入：先去掉 Word 存不了的控制字元。"""
    return INVALID_XML_CHARS.sub("", v or "").strip()


def _case_of(s: Session, draft_id: UUID) -> Case:
    return s.get(Case, s.get(Round, s.get(Draft, draft_id).round_id).case_id)


def _procedure(case: Case) -> str:
    return procedure_for(case.case_type, _clean(case.case_number))


def export_brief_title(s: Session, draft_id: UUID) -> str:
    """書狀名稱依程序（設計 5.1）：案號字別優先，否則用案型預設。下載檔名也用它。"""
    rnd = s.get(Round, s.get(Draft, draft_id).round_id)
    return brief_title(_procedure(_case_of(s, draft_id)), rnd.brief_kind)


def _attorneys(case: Case) -> list[str]:
    return [a for a in (_clean(x) for x in case.attorneys or []) if a]


def _party_lines(case: Case, parties: list[CaseParty]) -> list[str]:
    """依順序「角色　姓名」；有法定代理人下一行列出；我方（連續的一組）之後列訴訟代理人。"""
    counsel = [f"訴訟代理人　{a}" for a in _attorneys(case)]
    if not parties:  # 計畫 1 的案件沒有當事人：留我方與對造的位置給律師填
        other = "defendant" if case.our_side == "plaintiff" else "plaintiff"
        return [f"{SIDE_LABEL[case.our_side]}　", *counsel, f"{SIDE_LABEL[other]}　"]
    out: list[str] = []
    for i, p in enumerate(parties):
        out.append(f"{p.role}　{_clean(p.name)}")
        if _clean(p.legal_rep):
            out.append(f"法定代理人　{_clean(p.legal_rep)}")
        last_of_ours = p.alignment == "ours" and (i + 1 == len(parties) or parties[i + 1].alignment != "ours")
        if last_of_ours:
            out.extend(counsel)
    return out


def _cited_exhibits(s: Session, paragraph_ids: list[UUID]) -> list[tuple[Exhibit, str]]:
    rows = s.execute(
        select(Exhibit, Document.filename).join(Document, Document.id == Exhibit.document_id)
        .join(Page, Page.document_id == Document.id).join(PageText, PageText.page_id == Page.id)
        .join(Citation, Citation.page_text_id == PageText.id)
        .where(Citation.paragraph_id.in_(paragraph_ids)).distinct()
    ).all()
    return sorted(((ex, name) for ex, name in rows), key=lambda r: (r[0].prefix, r[0].number))


OPENING = {"complaint": "依法起訴事：", "answer": "依法提出答辯事：", "preparatory": "依法提出準備書狀事："}
SCHEDULE_COLUMNS = ("編號", "遺產項目", "價額", "分割方法", "應繼分")
SCHEDULE_ROWS = 4


def export_draft(s: Session, draft_id: UUID, member: CurrentMember, today: date | None = None) -> bytes:
    """狀頭內容與順序照設計 5.6；today 預設為台灣時區的今天（測試可指定）。"""
    # 先鎖住整份草稿的段落（依 id 排序避免互鎖），檢查到產生檔案之間別人不能改段落或確認引用
    s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id).order_by(Paragraph.id)
              .with_for_update(read=True)).all()
    blockers = export_blockers(s, draft_id)  # 一律重新驗證
    if blockers:
        raise ExportBlocked(blockers)
    paragraphs = body_paragraphs(s, draft_id)  # 聲明另外處理，不佔段落編號
    statement = statement_of(s, draft_id)
    rnd = s.get(Round, s.get(Draft, draft_id).round_id)
    kind = rnd.brief_kind

    case = _case_of(s, draft_id)
    parties = list(s.scalars(select(CaseParty).where(CaseParty.case_id == case.id).order_by(CaseParty.seq)))
    procedure = _procedure(case)

    d = docx.Document()
    _setup(d)
    title = d.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(export_brief_title(s, draft_id))
    run.bold, run.font.size = True, Pt(18)
    number = _clean(case.case_number) or ("（尚未分案）" if kind == "complaint" else "")  # 起訴時還沒分案
    d.add_paragraph(f"案號：{number}　股別：{_clean(case.division)}")
    for line in _party_lines(case, parties):
        d.add_paragraph(line)
    if kind == "complaint":
        label, value = claim_value(s, rnd)
        d.add_paragraph(f"{label}：新臺幣{value:,}元")
    d.add_paragraph(f"為{_clean(case.cause) or '　　'}事件，{OPENING[kind]}")
    if statement is not None:  # 訴之聲明、答辯聲明（設計 5.1）
        d.add_paragraph(STATEMENT_LABEL[kind])
        for line in render_text(s, statement).split("\n"):
            d.add_paragraph(line)
    if kind in BODY_LABEL:
        d.add_paragraph(BODY_LABEL[kind])
    for n, p in enumerate(paragraphs, start=1):
        d.add_paragraph(display_heading(n, p.heading))  # 編號依未刪除段落的順序重排，與畫面一致
        for line in render_text(s, p).split("\n"):
            d.add_paragraph(line)
    d.add_paragraph("謹狀")
    addressee = _clean(case.addressee) or _clean(default_addressee(case.court, procedure))
    d.add_paragraph(f"{addressee}　公鑒")
    if kind == "complaint" and case.case_type == "estate_partition":  # 分割遺產的附表由律師在 Word 填寫
        d.add_paragraph("附表")
        table = d.add_table(rows=1 + SCHEDULE_ROWS, cols=len(SCHEDULE_COLUMNS))
        table.style = "Table Grid"
        for cell, name in zip(table.rows[0].cells, SCHEDULE_COLUMNS):
            cell.text = name
    exhibits = _cited_exhibits(s, [p.id for p in paragraphs])
    if exhibits:
        d.add_paragraph("證物名稱及件數")
        for ex, filename in exhibits:
            d.add_paragraph(f"{ex.prefix}{ex.number}　{INVALID_XML_CHARS.sub('', filename or '')}")
    d.add_paragraph(roc_date(today or datetime.now(TAIPEI).date()))
    d.add_paragraph("具狀人：" + "、".join(_clean(p.name) for p in parties if p.alignment == "ours"))
    counsel = _attorneys(case)
    d.add_paragraph("撰狀人：" + (f"訴訟代理人{'、'.join(counsel)}" if counsel else ""))

    buf = io.BytesIO()
    d.save(buf)
    data = buf.getvalue()
    s.add(ExportRecord(firm_id=member.firm_id, draft_id=draft_id, member_id=member.id,
                       bar_number=member.bar_number, sha256=hashlib.sha256(data).hexdigest()))
    s.flush()
    return data
