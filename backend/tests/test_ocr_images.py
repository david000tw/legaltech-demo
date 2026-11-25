"""計畫 2A Task 3：照片合成一份 A4 PDF、每頁原圖、掃描後加文字層的判斷、照片上傳。"""
import io

import docx
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfReader
from sqlalchemy import select

from app.db import tenant_session
from app.main import create_app
from app.models import Document, Page
from app.ocr.images import looks_scanned_with_text, photos_to_pdf, render_pdf_page
from app.storage import LocalStorage
from tests.helpers import create_firm, create_member, login
from tests.pdf_helpers import image_pdf_with_text_layer
from tests.test_ingest import new_case, upload, use_fake_ocr


def photo(size=(600, 400), mode="RGB", fmt="JPEG", exif_orientation=None, label="收據") -> bytes:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([size[0] // 10, size[1] // 10, size[0] // 2, size[1] // 3], fill=(200, 30, 30))
    d.text((20, 20), label, fill="black")
    if mode != "RGB":
        img = img.convert(mode)
    buf = io.BytesIO()
    kw = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        exif[0x010F] = "PhoneMaker"
        kw["exif"] = exif.tobytes()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _page_sizes(pdf: bytes) -> list[tuple[float, float]]:
    return [(float(p.mediabox.width), float(p.mediabox.height)) for p in PdfReader(io.BytesIO(pdf)).pages]


def test_large_photo_fits_a4_and_small_png():
    pdf = photos_to_pdf([photo((4032, 3024))])
    (w, h), = _page_sizes(pdf)
    assert w <= 842.5 and h <= 595.5
    png = render_pdf_page(pdf, 1)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and max(img.size) <= 2000 and len(png) < 5 * 1024 * 1024


def test_render_caps_long_edge():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=2000, height=1000)  # 150 dpi 會變成 4167 像素寬
    buf = io.BytesIO()
    w.write(buf)
    img = Image.open(io.BytesIO(render_pdf_page(buf.getvalue(), 1)))
    assert max(img.size) == 2000


def test_photo_exif_rotated_and_odd_modes_converted():
    pdf = photos_to_pdf([photo((600, 400), exif_orientation=6), photo(mode="CMYK"),
                         photo(mode="RGBA", fmt="PNG"), photo(mode="P", fmt="PNG")])
    sizes = _page_sizes(pdf)
    assert len(sizes) == 4
    assert sizes[0][0] < sizes[0][1]  # 轉正後是直的
    assert b"Exif" not in pdf and b"PhoneMaker" not in pdf


def test_photos_to_pdf_keeps_order_and_page_count():
    pdf = photos_to_pdf([photo((300, 600)), photo((600, 300)), photo((300, 300))])
    sizes = _page_sizes(pdf)
    assert len(sizes) == 3
    assert sizes[0][0] < sizes[0][1] and sizes[1][0] > sizes[1][1] and abs(sizes[2][0] - sizes[2][1]) < 1


def test_unreadable_photo_names_its_position():
    import pytest

    from app.ingest.extract import UnreadableFile
    with pytest.raises(UnreadableFile, match="無法讀取第 2 張照片"):
        photos_to_pdf([photo(), b"not an image"])


def test_looks_scanned_with_text():
    assert looks_scanned_with_text(image_pdf_with_text_layer(photo((1240, 1754)), "Receipt total NT$ 12,000 paid")) is True
    assert looks_scanned_with_text(photos_to_pdf([photo()])) is False
    assert looks_scanned_with_text(b"%PDF-1.4 broken") is False


def _multi(client, case_id, items, kind="evidence"):
    return client.post(f"/api/cases/{case_id}/documents", data={"kind": kind},
                       files=[("files", (name, data)) for name, data in items])


def test_upload_multiple_photos_one_exhibit(lawyer_client):
    client, firm_id, _ = lawyer_client
    use_fake_ocr(client)
    case_id = new_case(client)
    a, b = photo(label="A"), photo(mode="RGBA", fmt="PNG", label="B")
    r = _multi(client, case_id, [("a.jpg", a), ("b.png", b)])
    assert r.status_code == 200, r.text
    assert r.json()["page_count"] == 2
    assert r.json()["ocr"] == {"status": "running", "pages": 2, "minutes": 1}
    detail = client.get(f"/api/cases/{case_id}").json()
    assert [e["label"] for e in detail["exhibits"]] == ["原證1"]
    storage = LocalStorage(client.app.state.settings.storage_dir)
    with tenant_session(firm_id) as s:
        doc = s.get(Document, r.json()["id"])
        assert doc.filename == "a（2張照片）.pdf"
        assert [x["filename"] for x in doc.source_images] == ["a.jpg", "b.png"]
        assert [storage.get(x["key"]) for x in doc.source_images] == [a, b]
        pages = s.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)).all()
        assert [p.ocr_state for p in pages] == ["done", "done"]  # 上傳後自動辨識（假的 AI）
        assert storage.get(doc.storage_key).startswith(b"%PDF")


def test_upload_single_photo_named_pdf(lawyer_client):
    client, firm_id, _ = lawyer_client
    use_fake_ocr(client)
    case_id = new_case(client)
    r = upload(client, case_id, "診斷證明.JPG", photo())
    assert r.status_code == 200, r.text
    with tenant_session(firm_id) as s:
        assert s.get(Document, r.json()["id"]).filename == "診斷證明.pdf"


def test_upload_rejects_heic_and_mixed(lawyer_client):
    client, _, _ = lawyer_client
    case_id = new_case(client)
    r = upload(client, case_id, "IMG_0001.HEIC", b"heic-bytes")
    assert r.status_code == 415 and r.json()["detail"] == "不支援 HEIC 照片，請在手機上改存成 JPG 後再上傳"
    r = _multi(client, case_id, [("a.jpg", photo()), ("b.pdf", b"%PDF-1.4")])
    assert r.status_code == 422 and r.json()["detail"].startswith("一次選多個檔案時只能都是照片（JPG、PNG）")
    r = _multi(client, case_id, [(f"p{i}.jpg", photo((60, 40))) for i in range(31)])
    assert r.status_code == 422 and r.json()["detail"] == "一次最多上傳 30 張照片"
    assert client.get(f"/api/cases/{case_id}").json()["documents"] == []


def test_upload_sets_scan_hint(lawyer_client):
    client, firm_id, _ = lawyer_client
    case_id = new_case(client)
    r = upload(client, case_id, "掃描.pdf", image_pdf_with_text_layer(photo((1240, 1754)), "Receipt total 12,000 paid"))
    assert r.status_code == 200, r.text
    with tenant_session(firm_id) as s:
        doc = s.get(Document, r.json()["id"])
        assert doc.scan_hint is True
        assert {p.ocr_state for p in s.scalars(select(Page).where(Page.document_id == doc.id))} == {"none"}


def test_page_image_endpoint(lawyer_client, db):
    client, firm_id, _ = lawyer_client
    use_fake_ocr(client)
    case_id = new_case(client)
    doc_id = upload(client, case_id, "收據.jpg", photo()).json()["id"]
    d = docx.Document()
    d.add_paragraph("診斷：右腕骨折。")
    buf = io.BytesIO()
    d.save(buf)
    docx_id = upload(client, case_id, "診斷.docx", buf.getvalue()).json()["id"]
    with tenant_session(firm_id) as s:
        page_id = s.scalars(select(Page.id).where(Page.document_id == doc_id)).one()
        docx_page = s.scalars(select(Page.id).where(Page.document_id == docx_id)).one()
    r = client.get(f"/api/pages/{page_id}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(r.content)).format == "PNG"
    with tenant_session(firm_id) as s:
        key = s.get(Page, page_id).image_key
    assert key and client.get(f"/api/pages/{page_id}/image").content == r.content
    with tenant_session(firm_id) as s:
        assert s.get(Page, page_id).image_key == key
    r = client.get(f"/api/pages/{docx_page}/image")
    assert r.status_code == 404 and r.json()["detail"] == "這一頁沒有原圖（Word 或純文字檔）"
    other = create_firm("乙事務所")
    create_member(other, "b@other.test")
    c2 = TestClient(create_app(db))
    assert login(c2, "b@other.test").status_code == 200
    r = c2.get(f"/api/pages/{page_id}/image")
    assert r.status_code == 404 and r.json()["detail"] == "找不到頁面"


def test_first_render_is_idempotent_under_concurrent_requests(lawyer_client):
    """C7：兩個請求同時第一次要同一頁原圖：存放鍵固定，只留下一個 PNG，資料庫指向同一把鍵。"""
    from pathlib import Path

    from app.ocr.images import ensure_page_image
    from tests.test_ingest import FIXTURES
    client, firm_id, _ = lawyer_client
    case_id = new_case(client)  # 有文字層的 PDF：上傳時不辨識，所以還沒有原圖
    doc_id = upload(client, case_id, "判決.pdf", (FIXTURES / "tcev_113_3946.pdf").read_bytes()).json()["id"]
    storage = LocalStorage(client.app.state.settings.storage_dir)
    with tenant_session(firm_id) as s:
        page_id = s.scalars(select(Page.id).where(Page.document_id == doc_id, Page.page_no == 1)).one()
        assert s.get(Page, page_id).image_key is None
    with tenant_session(firm_id) as s1, tenant_session(firm_id) as s2:  # 兩個請求都還沒看到對方寫的鍵
        k1 = ensure_page_image(s1, storage, s1.get(Page, page_id))
        k2 = ensure_page_image(s2, storage, s2.get(Page, page_id))
    assert k1 == k2
    with tenant_session(firm_id) as s:
        assert s.get(Page, page_id).image_key == k1
    folder = Path(client.app.state.settings.storage_dir) / str(firm_id) / str(case_id)
    assert len(list(folder.glob("*.png"))) == 1
    assert Image.open(io.BytesIO(storage.get(k1))).format == "PNG"
    assert [p.name for p in folder.iterdir() if p.name.startswith(".tmp")] == []  # 沒有留下暫存檔


# ---- 小問題批次（C8）：上傳訊息與壞圖片 ----
def test_unsupported_format_message_mentions_photos(lawyer_client):
    """C8b：WebP、GIF 被拒時，訊息要說 JPG、PNG 照片可以上傳。"""
    client, _, _ = lawyer_client
    case_id = new_case(client)
    for name, ext in (("相片.webp", ".webp"), ("動畫.gif", ".gif")):
        r = upload(client, case_id, name, b"RIFF....WEBPVP8 ")
        assert r.status_code == 415, r.text
        detail = r.json()["detail"]
        assert detail.startswith(f"不支援的檔案格式：{ext}")
        assert ".pdf、.docx、.txt" in detail and "JPG" in detail and "PNG" in detail


def test_multiple_non_photo_files_message_says_one_by_one(lawyer_client):
    """C8a：一次選好幾份 PDF 回 422；訊息要說 PDF、Word、純文字要一份一份上傳。"""
    client, _, _ = lawyer_client
    case_id = new_case(client)
    r = _multi(client, case_id, [("a.pdf", b"%PDF-1.4"), ("b.pdf", b"%PDF-1.4")])
    assert r.status_code == 422
    assert r.json()["detail"] == "一次選多個檔案時只能都是照片（JPG、PNG）；PDF、Word、純文字請一份一份上傳"
    assert client.get(f"/api/cases/{case_id}").json()["documents"] == []


def test_late_pillow_failure_is_unreadable_not_500(lawyer_client, monkeypatch):
    """C8c：貼圖、縮圖階段才出錯的壞圖片，也要回一般的 422「無法讀取第 N 張照片」，不是 500。"""
    from app.ocr.images import _clean_rgb
    from app.ingest.extract import UnreadableFile

    def boom(self, *a, **k):
        raise OSError("image file is truncated")
    monkeypatch.setattr(Image.Image, "thumbnail", boom)
    try:
        _clean_rgb(photo(), 2)
    except UnreadableFile as e:
        assert str(e) == "無法讀取第 2 張照片"
    else:
        raise AssertionError("沒有丟出 UnreadableFile")
    client, _, _ = lawyer_client
    case_id = new_case(client)
    r = upload(client, case_id, "收據.jpg", photo())
    assert r.status_code == 422 and r.json()["detail"] == "無法讀取第 1 張照片"
