import io
from pathlib import Path

import docx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from app.db import tenant_session
from app.ingest.extract import UnsupportedFile, extract_pages
from app.ingest.service import current_page_text
from app.models import Document, Page, PageText

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def blank_pdf(pages: int = 1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def new_case(client, our_side="plaintiff") -> str:
    r = client.post("/api/cases", json={"title": "林大明車禍案", "our_side": our_side})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def upload(client, case_id, filename, data, kind="evidence", round_id=None):
    form = {"kind": kind}
    if round_id:
        form["round_id"] = round_id
    return client.post(f"/api/cases/{case_id}/documents", data=form, files={"file": (filename, data)})


def test_pdf_text_layer_pages():
    pages = extract_pages((FIXTURES / "tcev_113_3946.pdf").read_bytes(), "判決.pdf")
    assert len(pages) == 7
    assert [p.page_no for p in pages] == list(range(1, 8))
    assert "113年度中簡字第3946號" in pages[0].text
    assert all(p.text for p in pages)


def test_blank_pdf_marks_needs_ocr(lawyer_client):
    client, firm_id, _ = lawyer_client
    case_id = new_case(client)
    r = upload(client, case_id, "掃描檔.pdf", blank_pdf(2))
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    with tenant_session(firm_id) as s:
        pages = s.scalars(select(Page).where(Page.document_id == doc_id)).all()
        assert len(pages) == 2 and all(p.needs_ocr for p in pages)
        assert s.scalars(select(PageText).where(PageText.page_id.in_([p.id for p in pages]))).all() == []
        assert current_page_text(s, pages[0].id) is None


def test_chinese_filename_not_in_storage_key(lawyer_client):
    client, firm_id, _ = lawyer_client
    case_id = new_case(client)
    name = "原證1_診斷證明書（林口長庚）.pdf"
    r = upload(client, case_id, name, blank_pdf())
    assert r.status_code == 200, r.text
    with tenant_session(firm_id) as s:
        d = s.get(Document, r.json()["id"])
        assert d.filename == name
        assert "診斷" not in d.storage_key and d.storage_key.isascii()
        assert d.storage_key.startswith(f"{firm_id}/{case_id}/") and d.storage_key.endswith(".pdf")


def test_exhibit_numbering_plaintiff_and_defendant(lawyer_client):
    client, _, _ = lawyer_client
    p_case = new_case(client, "plaintiff")
    upload(client, p_case, "01_診斷證明書.txt", "診斷：右腕骨折，宜休養六週。".encode())
    upload(client, p_case, "02_收據.txt", "醫療費用收據新臺幣12,000元整。".encode())
    labels = [e["label"] for e in client.get(f"/api/cases/{p_case}").json()["exhibits"]]
    assert labels == ["原證1", "原證2"]

    d_case = new_case(client, "defendant")
    upload(client, d_case, "01_行車紀錄器截圖說明.txt", "原告機車於路口未減速。".encode())
    labels = [e["label"] for e in client.get(f"/api/cases/{d_case}").json()["exhibits"]]
    assert labels == ["被證1"]


def test_opponent_brief_gets_no_exhibit_and_text_is_readable(lawyer_client):
    client, _, _ = lawyer_client
    case_id = new_case(client)
    rnd = client.post(f"/api/cases/{case_id}/rounds").json()
    assert rnd["seq"] == 1
    body = "民事答辯狀\n被告否認有過失。\f第二頁：原告未減速慢行。".encode()
    r = upload(client, case_id, "答辯狀.txt", body, kind="opponent_brief", round_id=rnd["id"])
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["exhibits"] == []
    doc = detail["documents"][0]
    assert doc["kind"] == "opponent_brief" and doc["round_id"] == rnd["id"]
    assert len(doc["pages"]) == 2
    page = client.get(f"/api/pages/{doc['pages'][1]['id']}").json()
    assert "原告未減速慢行" in page["text"]


def test_docx_page_breaks_split_pages():
    d = docx.Document()
    d.add_paragraph("第一頁內容：被告主張原告超速。")
    d.add_page_break()
    d.add_paragraph("第二頁內容：慰撫金過高。")
    buf = io.BytesIO()
    d.save(buf)
    pages = extract_pages(buf.getvalue(), "答辯狀.docx")
    assert len(pages) == 2
    assert "原告超速" in pages[0].text and "慰撫金過高" in pages[1].text


def test_extract_rejects_unknown_extension():
    with pytest.raises(UnsupportedFile):
        extract_pages(b"abc", "照片.jpg")


def test_unsupported_extension_415(lawyer_client):
    client, _, _ = lawyer_client
    case_id = new_case(client)
    assert upload(client, case_id, "照片.jpg", b"\xff\xd8\xff").status_code == 415
