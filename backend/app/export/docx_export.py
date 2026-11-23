"""匯出 Word：只有全部段落已審、全部引用通過驗證且判決逐筆確認後才能匯出。"""
from __future__ import annotations

import hashlib
import io
from uuid import UUID

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.models import Citation, Document, Exhibit, ExportRecord, Page, PageText, Paragraph
from app.render import render_text
from app.verify.citations import INVALID_XML_CHARS, Blocker, export_blockers

FONT = "標楷體"
TITLE = "民事準備書狀"


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


def _cited_exhibits(s: Session, paragraph_ids: list[UUID]) -> list[tuple[Exhibit, str]]:
    rows = s.execute(
        select(Exhibit, Document.filename).join(Document, Document.id == Exhibit.document_id)
        .join(Page, Page.document_id == Document.id).join(PageText, PageText.page_id == Page.id)
        .join(Citation, Citation.page_text_id == PageText.id)
        .where(Citation.paragraph_id.in_(paragraph_ids)).distinct()
    ).all()
    return sorted(((ex, name) for ex, name in rows), key=lambda r: (r[0].prefix, r[0].number))


def export_draft(s: Session, draft_id: UUID, member: CurrentMember) -> bytes:
    # 先鎖住整份草稿的段落（依 id 排序避免互鎖），檢查到產生檔案之間別人不能改段落或確認引用
    s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id).order_by(Paragraph.id)
              .with_for_update(read=True)).all()
    blockers = export_blockers(s, draft_id)  # 一律重新驗證
    if blockers:
        raise ExportBlocked(blockers)
    paragraphs = list(s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id).order_by(Paragraph.seq)))

    d = docx.Document()
    _setup(d)
    title = d.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(TITLE)
    run.bold, run.font.size = True, Pt(18)
    for p in paragraphs:
        if p.heading.strip():
            d.add_paragraph(p.heading)
        for line in render_text(s, p).split("\n"):
            d.add_paragraph(line)
    exhibits = _cited_exhibits(s, [p.id for p in paragraphs])
    if exhibits:
        d.add_paragraph("證物清單")
        for ex, filename in exhibits:
            d.add_paragraph(f"{ex.prefix}{ex.number}　{INVALID_XML_CHARS.sub('', filename or '')}")

    buf = io.BytesIO()
    d.save(buf)
    data = buf.getvalue()
    s.add(ExportRecord(firm_id=member.firm_id, draft_id=draft_id, member_id=member.id,
                       bar_number=member.bar_number, sha256=hashlib.sha256(data).hexdigest()))
    s.flush()
    return data
