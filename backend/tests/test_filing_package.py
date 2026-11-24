"""子專案丙 Task 5：電子遞狀包（設計 5.11）：書狀 Word、證物清單、引用到的證物原檔、說明。"""
import hashlib
import io
import zipfile

import docx
from sqlalchemy import func, select

from app.db import admin_session, tenant_session
from app.export.docx_export import export_brief_title
from app.export.package import safe_filename
from app.ingest.service import current_page_text, ingest_document
from app.models import Candidate, Document, Exhibit, ExportRecord, Page
from tests.export_helpers import clients, force_reviewed
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT, J_REASONING, make_paragraph, setup_verify_round

NOTE = "司法院線上遞狀需 PDF 與憑證簽章；請用 Word 另存 PDF 後，於司法院系統簽章送出。"
TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"


def ready(db_):
    ctx = setup_verify_round()
    make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)], heading="㈠關於路權")
    return ctx, clients(ctx, db_)


def _records(kind=None) -> int:
    with admin_session() as s:
        q = select(func.count()).select_from(ExportRecord)
        if kind:
            q = q.where(ExportRecord.kind == kind)
        return s.scalar(q)


def _package(client, ctx):
    return client.post(f"/api/drafts/{ctx['draft_id']}/filing-package")


def _title(ctx) -> str:
    with tenant_session(ctx["firm"]) as s:
        return export_brief_title(s, ctx["draft_id"])


def test_package_requires_lawyer(db):
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert _package(cs["assistant"], ctx).status_code == 403
    assert _records() == 0


def test_package_blocked_until_reviewed(db):
    ctx, cs = ready(db)
    r = _package(cs["lawyer"], ctx)
    assert r.status_code == 409
    assert any(b["reason"] == "unreviewed" for b in r.json()["blockers"])
    assert _records() == 0


def test_package_contents_and_record(db):
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = _package(cs["lawyer"], ctx)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    title = _title(ctx)
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.namelist() == [f"01_{title}.docx", "02_證物清單.docx", "03_原證1_01_研判表.txt", "說明.txt"]
    assert z.read("03_原證1_01_研判表.txt") == EVIDENCE_TEXT.encode("utf-8")
    assert z.read("說明.txt").decode("utf-8") == NOTE
    assert "關於路權" in "\n".join(p.text for p in docx.Document(io.BytesIO(z.read(f"01_{title}.docx"))).paragraphs)
    with admin_session() as s:
        rec = s.scalars(select(ExportRecord)).one()
        assert rec.kind == "package" and rec.sha256 == hashlib.sha256(r.content).hexdigest()


def test_exhibit_list_docx(db):
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    z = zipfile.ZipFile(io.BytesIO(_package(cs["lawyer"], ctx).content))
    table = docx.Document(io.BytesIO(z.read("02_證物清單.docx"))).tables[0]
    rows = [[c.text for c in row.cells] for row in table.rows]
    assert rows == [["證物號碼", "名稱", "頁數"], ["原證1", "01_研判表.txt", "1"]]


EXTRA = {  # 依上傳順序成為原證2、3、4
    "a/b:c?.txt": "證人王小明證稱事故當時天候晴朗且路面乾燥視線良好無遮蔽。",
    "同名.txt#1": "修車廠估價單記載前保險桿與右側葉子板均需更換費用共計四萬五千元。",
    "同名.txt#2": "診所收據記載原告自事故後每週復健二次並自費購買護具一副。",
}


def test_package_filenames_sanitized_and_ordered(db):
    ctx = setup_verify_round()
    cs = clients(ctx, db)
    codes = []
    with tenant_session(ctx["firm"]) as s:
        for i, (name, body) in enumerate(EXTRA.items(), start=2):
            doc = ingest_document(s, ctx["firm"], ctx["case_id"], "evidence", name.split("#")[0], body.encode("utf-8"))
            page = s.scalars(select(Page).where(Page.document_id == doc.id)).one()
            s.add(Candidate(firm_id=ctx["firm"], round_id=ctx["round_id"], code=f"E{i}", kind="evidence",
                            page_text_id=current_page_text(s, page.id).id))
            codes.append((f"E{i}", body))
        s.flush()
        renamed = s.scalars(select(Exhibit).join(Document, Document.id == Exhibit.document_id)
                            .where(Document.filename == "a/b:c?.txt")).one()
        assert renamed.number == 2
        renamed.number = 5  # 原證2 改號成原證5
    text = "此有" + "、".join("{{cite:%s}}" % c for c in ["E1", *(c for c, _ in codes)]) + "可稽。"
    make_paragraph(ctx, text, [("E1", E_QUOTE), *codes], heading="㈠證據")
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = _package(cs["lawyer"], ctx)
    assert r.status_code == 200, r.text
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names[2:] == ["03_原證1_01_研判表.txt", "04_原證3_同名.txt", "05_原證4_同名.txt", "06_原證5_abc.txt",
                         "說明.txt"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.read("04_原證3_同名.txt") != z.read("05_原證4_同名.txt")


def test_safe_filename():
    assert safe_filename('a\\b/c:d*e?f"g<h>i|j\x01k') == "abcdefghijk"
    assert safe_filename("  ..報告.. ") == "報告"
    assert safe_filename("///") == "未命名"
    long = "字" * 200 + ".pdf"
    out = safe_filename(long)
    assert len(out) == 120 and out.endswith(".pdf")


def test_export_docx_unchanged(db):
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200
    assert _records("docx") == 1 and _records("package") == 0
