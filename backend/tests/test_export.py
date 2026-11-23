import io

import docx
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from sqlalchemy import select

from app.db import tenant_session
from app.models import Citation, Exhibit, ExportRecord
from tests.export_helpers import BAR, clients, force_reviewed
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"


def ready(db):
    ctx = setup_verify_round()
    make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)], heading="㈠關於路權")
    cs = clients(ctx, db)
    return ctx, cs


def review_all(ctx, lawyer):
    with tenant_session(ctx["firm"]) as s:
        cid = s.scalars(select(Citation).where(Citation.code == "J1")).one().id
        from app.models import Paragraph
        pid = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == ctx["draft_id"])).one()
    assert lawyer.post(f"/api/citations/{cid}/confirm").status_code == 200
    assert lawyer.post(f"/api/paragraphs/{pid}/review").status_code == 200


def all_text(d: docx.Document) -> str:
    return "\n".join(p.text for p in d.paragraphs)


def test_export_blocked_when_unreviewed_409(db):
    ctx, cs = ready(db)
    r = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409
    assert {"paragraph_seq": 1, "reason": "unreviewed"} in r.json()["blockers"]


def test_assistant_export_403(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    assert cs["assistant"].get(f"/api/drafts/{ctx['draft_id']}/export").status_code == 403


def test_export_docx_format(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    r = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    d = docx.Document(io.BytesIO(r.content))
    sec = d.sections[0]
    for m in (sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin):
        assert abs(m - Cm(2.5)) < Cm(0.01)  # Word 以 twip 儲存，2.5 cm = 1417 twip
    normal = d.styles["Normal"]
    assert normal.font.size == Pt(14)
    assert normal.element.rPr.rFonts.get(qn("w:eastAsia")) == "標楷體"
    assert normal.paragraph_format.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    assert normal.paragraph_format.line_spacing == Pt(24)
    title = d.paragraphs[0]
    assert title.text == "民事準備書狀"
    assert all(run.bold and run.font.size == Pt(18) for run in title.runs)


def test_export_has_no_markers_and_renders_exhibit(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    d = docx.Document(io.BytesIO(cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export").content))
    text = all_text(d)
    assert "{{" not in text and "原證1第1頁" in text and "㈠關於路權" in text
    assert "臺灣臺中地方法院113年度中簡字第3946號民事判決" in text and f"「{J_REASONING}」" in text
    assert "證物清單" in text and "原證1" in text.split("證物清單")[1] and "01_研判表.txt" in text


def test_export_uses_renumbered_exhibit(db):
    ctx, cs = ready(db)
    with tenant_session(ctx["firm"]) as s:
        ex = s.scalars(select(Exhibit).where(Exhibit.document_id == ctx["exhibit_doc"])).one().id
    assert cs["lawyer"].patch(f"/api/exhibits/{ex}", json={"number": 5}).status_code == 200
    review_all(ctx, cs["lawyer"])
    text = all_text(docx.Document(io.BytesIO(cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export").content)))
    assert "原證5第1頁" in text and "原證1第1頁" not in text


def test_export_writes_record_without_content(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    r = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export")
    import hashlib
    with tenant_session(ctx["firm"]) as s:
        rec = s.scalars(select(ExportRecord)).one()
        assert rec.bar_number == BAR and rec.sha256 == hashlib.sha256(r.content).hexdigest()
        assert rec.draft_id == ctx["draft_id"]


def test_force_reviewed_valid_draft_exports(db):
    """對照組：內容正確時，強制設成已審也能匯出（證明紅隊測試的 409 不是被 force_reviewed 害的）。"""
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200
