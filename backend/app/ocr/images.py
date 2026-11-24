"""照片合成 PDF、每頁原圖、掃描後加文字層的判斷（計畫 2A，設計 6.1）。

全系統只處理 PDF：照片在上傳時縮成 A4 合成一份 PDF（不帶 EXIF），原圖由 PDF 轉成 PNG。
"""
from __future__ import annotations

import io

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app.ingest.extract import UnreadableFile, file_ext
from app.models import Document, Page
from app.ocr.state import A4_PX, MAX_IMAGE_EDGE, PAGE_IMAGE_DPI
from app.storage import LocalStorage

IMAGE_EXTS = (".jpg", ".jpeg", ".png")
HEIC_EXTS = (".heic", ".heif")
MAX_PHOTOS = 30
SCAN_IMAGE_RATIO = 0.9  # 圖片物件蓋住頁面 90% 以上＝整頁是掃描圖
SCAN_CHECK_PAGES = 3
HEIC_MESSAGE = "不支援 HEIC 照片，請在手機上改存成 JPG 後再上傳"
MIXED_MESSAGE = "一次上傳多個檔案時只能都是照片（JPG、PNG）"
TOO_MANY_PHOTOS = f"一次最多上傳 {MAX_PHOTOS} 張照片"
NO_IMAGE = "這一頁沒有原圖（Word 或純文字檔）"


def _clean_rgb(data: bytes, n: int) -> Image.Image:
    """讀一張照片：依 EXIF 轉正、轉成 RGB，並重新建立影像（不帶任何中繼資料）。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as e:  # Pillow 對壞檔會丟各種例外
        raise UnreadableFile(f"無法讀取第 {n} 張照片") from e
    if img.mode in ("RGBA", "LA", "P", "PA"):
        rgba = img.convert("RGBA")  # 透明的地方鋪白底，不變成黑色
        clean = Image.new("RGB", rgba.size, "white")
        clean.paste(rgba, mask=rgba.getchannel("A"))
    else:
        clean = Image.new("RGB", img.size, "white")
        clean.paste(img.convert("RGB"))
    box = A4_PX if clean.height >= clean.width else (A4_PX[1], A4_PX[0])
    clean.thumbnail(box, Image.Resampling.LANCZOS)  # 只縮不放大
    return clean


def photos_to_pdf(photos: list[bytes]) -> bytes:
    pages = [_clean_rgb(data, n) for n, data in enumerate(photos, start=1)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=PAGE_IMAGE_DPI, quality=85)
    return buf.getvalue()


def render_pdf_page(data: bytes, page_no: int) -> bytes:
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_no - 1]
        w, h = page.get_size()
        scale = PAGE_IMAGE_DPI / 72
        if max(w, h) * scale > MAX_IMAGE_EDGE:
            scale = MAX_IMAGE_EDGE / max(w, h)
        img = page.render(scale=scale).to_pil().convert("RGB")
        if max(img.size) > MAX_IMAGE_EDGE:  # 捨入可能多 1 像素
            img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()


def ensure_page_image(s: Session, storage: LocalStorage, page: Page) -> str | None:
    """這一頁的原圖存放鍵；第一次需要時從 PDF 轉圖並保存。Word、純文字沒有原圖。自己不上鎖。"""
    if page.image_key:
        return page.image_key
    doc = s.get(Document, page.document_id)
    if file_ext(doc.filename) != ".pdf":
        return None
    png = render_pdf_page(storage.get(doc.storage_key), page.page_no)
    page.image_key = storage.put(doc.firm_id, doc.case_id, ".png", png)
    return page.image_key


def _has_big_image(page) -> bool:
    w, h = page.get_size()
    area = w * h
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=2):
        left, bottom, right, top = obj.get_bounds()
        if area and (right - left) * (top - bottom) >= SCAN_IMAGE_RATIO * area:
            return True
    return False


def looks_scanned_with_text(data: bytes) -> bool:
    """前 3 頁之中，任一頁整頁是圖片、同時又有文字層＝可能是掃描後加上的文字（不可靠）。出錯一律 False。"""
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        return False
    try:
        for i in range(min(len(pdf), SCAN_CHECK_PAGES)):
            page = pdf[i]
            text = page.get_textpage().get_text_range()
            if text.strip() and _has_big_image(page):
                return True
        return False
    except Exception:
        return False
    finally:
        pdf.close()
