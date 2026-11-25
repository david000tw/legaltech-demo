"""各格式抽文字，逐頁回傳。PDF 沒有文字層（掃描檔）或單頁抽字失敗的頁回傳 text=None。

無法讀取的檔案（損壞、加密、空檔、無法辨識編碼）一律丟 UnreadableFile，不讓伺服器崩潰。
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

import docx
from docx.oxml.ns import qn
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.text_norm import normalize_text

MIN_PDF_PAGE_CHARS = 20
SUPPORTED = (".pdf", ".docx", ".txt")


NO_READABLE_TEXT = "此檔沒有可讀文字，請改上傳 PDF 或圖片"


class UnsupportedFile(Exception):
    pass


class UnreadableFile(Exception):
    pass


@dataclass(frozen=True)
class PageExtract:
    page_no: int
    text: str | None


def file_ext(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def _clean(text: str) -> str:
    return text.replace("\x00", "")  # PostgreSQL 文字欄位不接受 NUL


def extract_pages(data: bytes, filename: str) -> list[PageExtract]:
    ext = file_ext(filename)
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"不支援的檔案格式：{ext or '（無副檔名）'}，請上傳 {'、'.join(SUPPORTED)}，或 JPG、PNG 照片")
    if not data:
        raise UnreadableFile("檔案是空的")
    try:
        texts = {".pdf": _pdf, ".docx": _docx, ".txt": _txt}[ext](data)
    except UnreadableFile:
        raise
    except (PyPdfError, zipfile.BadZipFile, KeyError, ValueError, OSError) as e:
        raise UnreadableFile(f"無法讀取檔案（可能已損壞或加密）：{type(e).__name__}") from e
    pages = [PageExtract(page_no=i, text=None if t is None else _clean(t)) for i, t in enumerate(texts, start=1)]
    if ext != ".pdf" and not any((p.text or "").strip() for p in pages):
        # Word、純文字沒有原圖可辨識（辨識只處理 PDF 與照片）：整份沒有字就直接說清楚，不建出空白頁
        raise UnreadableFile(NO_READABLE_TEXT)
    return pages


def _pdf(data: bytes) -> list[str | None]:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UnreadableFile("PDF 有密碼保護，請先解除密碼再上傳")
    out: list[str | None] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:  # 單頁內容損壞：當作沒有文字層，等辨識
            t = ""
        out.append(t if len(normalize_text(t)) >= MIN_PDF_PAGE_CHARS else None)
    if not out:
        raise UnreadableFile("PDF 沒有任何頁面")
    return out


def _paragraph_runs(p, pages: list[list[str]]) -> None:
    """把段落文字加到目前頁；遇到分頁符號就開新頁。"""
    ppr = p.pPr
    if ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None and pages[-1]:
        pages.append([])
    buf: list[str] = []
    for run in p.iter(qn("w:r")):
        for child in run:
            if child.tag == qn("w:t"):
                buf.append(child.text or "")
            elif child.tag == qn("w:tab"):
                buf.append("\t")
            elif child.tag == qn("w:br") and child.get(qn("w:type")) == "page":
                pages[-1].append("".join(buf))
                buf = []
                pages.append([])
            elif child.tag in (qn("w:br"), qn("w:cr")):
                buf.append("\n")
    pages[-1].append("".join(buf))


def _table_rows(tbl) -> list[str]:
    rows = []
    for tr in tbl.iter(qn("w:tr")):
        cells = []
        for tc in tr.iter(qn("w:tc")):
            cells.append("".join(t.text or "" for t in tc.iter(qn("w:t"))))
        rows.append("\t".join(cells))
    return rows


def _docx(data: bytes) -> list[str]:
    body = docx.Document(io.BytesIO(data)).element.body
    pages: list[list[str]] = [[]]

    def walk(parent) -> None:
        for child in parent.iterchildren():
            if child.tag == qn("w:p"):
                _paragraph_runs(child, pages)
            elif child.tag == qn("w:tbl"):
                pages[-1].extend(_table_rows(child))
            elif child.tag == qn("w:sdt"):  # 內容控制項（範本、表單常見）：文字在 w:sdtContent 裡
                content = child.find(qn("w:sdtContent"))
                if content is not None:
                    walk(content)

    walk(body)
    texts = ["\n".join(lines).strip("\n") for lines in pages]
    while len(texts) > 1 and not texts[-1].strip():
        texts.pop()
    return texts


def _txt(data: bytes) -> list[str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        for enc in ("utf-8-sig", "cp950"):  # cp950：台灣常見的 Big5 純文字檔
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UnreadableFile("無法辨識純文字檔的編碼，請另存為 UTF-8")
    return text.replace("\r\n", "\n").split("\f")
