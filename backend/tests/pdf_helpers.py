"""測試用的 PDF：手工組出「掃描後加上文字層」的 PDF（整頁一張圖＋一行文字）。"""
from __future__ import annotations

import io

from PIL import Image

PAGE_W, PAGE_H = 595, 842


def _jpeg(png_or_jpeg: bytes) -> tuple[bytes, int, int]:
    img = Image.open(io.BytesIO(png_or_jpeg)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue(), img.width, img.height


def image_pdf_with_text_layer(png_or_jpeg: bytes, text: str) -> bytes:
    """一頁 A4：整頁一個 DCT 圖片物件，加上 Helvetica 的一行文字（text 只能是 ASCII）。"""
    jpg, w, h = _jpeg(png_or_jpeg)
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = (f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im0 Do Q\n"
               f"BT /F1 12 Tf 40 {PAGE_H - 40} Td ({safe}) Tj ET\n").encode("ascii")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
         f"/Resources << /XObject << /Im0 4 0 R >> /Font << /F1 5 0 R >> >> /Contents 6 0 R >>").encode(),
        (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
         f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg)} >>\nstream\n").encode() + jpg + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
