"""各格式抽文字，逐頁回傳。PDF 沒有文字層（掃描檔）的頁回傳 text=None。"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

import docx
from docx.oxml.ns import qn
from pypdf import PdfReader

from app.text_norm import normalize_text

MIN_PDF_PAGE_CHARS = 20
SUPPORTED = (".pdf", ".docx", ".txt")


class UnsupportedFile(Exception):
    pass


@dataclass(frozen=True)
class PageExtract:
    page_no: int
    text: str | None


def file_ext(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def extract_pages(data: bytes, filename: str) -> list[PageExtract]:
    ext = file_ext(filename)
    if ext == ".pdf":
        texts = _pdf(data)
    elif ext == ".docx":
        texts = _docx(data)
    elif ext == ".txt":
        texts = _txt(data)
    else:
        raise UnsupportedFile(f"不支援的檔案格式：{ext or '（無副檔名）'}，請上傳 {'、'.join(SUPPORTED)}")
    return [PageExtract(page_no=i, text=t) for i, t in enumerate(texts, start=1)]


def _pdf(data: bytes) -> list[str | None]:
    out: list[str | None] = []
    for page in PdfReader(io.BytesIO(data)).pages:
        t = page.extract_text() or ""
        out.append(t if len(normalize_text(t)) >= MIN_PDF_PAGE_CHARS else None)
    return out


def _docx(data: bytes) -> list[str]:
    pages: list[list[str]] = [[]]
    for para in docx.Document(io.BytesIO(data)).paragraphs:
        p = para._p
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
    texts = ["\n".join(lines).strip("\n") for lines in pages]
    while len(texts) > 1 and not texts[-1].strip():
        texts.pop()
    return texts


def _txt(data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp950")  # 台灣常見的 Big5 純文字檔
    text = text.replace("\r\n", "\n")
    return text.split("\f")
