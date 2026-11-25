"""電子遞狀包（設計 5.11）：書狀 Word、證物清單、書狀引用到的證物原檔、說明，打包成一個 zip。

司法院線上遞狀要 PDF 與憑證簽章，系統不代為轉檔或送出；說明檔提醒律師下一步。
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import PurePath
from uuid import UUID

import docx
from sqlalchemy.orm import Session

from app.auth import CurrentMember
from app.export.docx_export import _cited_exhibits, _clean, export_brief_title, render_draft
from app.models import Document
from app.storage import LocalStorage
from app.verify.citations import body_paragraphs

NOTE = "司法院線上遞狀需 PDF 與憑證簽章；請用 Word 另存 PDF 後，於司法院系統簽章送出。"
MAX_NAME = 120
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def safe_filename(name: str) -> str:
    """去掉 Windows 不接受的字元與控制字元、頭尾空白與句點；最長 120 字（保留副檔名）。"""
    out = _BAD.sub("", name or "").strip().strip(".").strip()
    if len(out) > MAX_NAME:
        ext = PurePath(out).suffix
        ext = ext if 0 < len(ext) <= 10 else ""
        out = out[:MAX_NAME - len(ext)] + ext
    return out or "未命名"


def _exhibit_list(rows: list[tuple[str, str, int]]) -> bytes:
    d = docx.Document()
    d.add_paragraph("證物清單")
    table = d.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, name in zip(table.rows[0].cells, ("證物號碼", "名稱", "頁數")):
        cell.text = name
    for label, name, pages in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = _clean(label), _clean(name), str(pages)  # 檔名可能夾控制字元，Word 存不了
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def build_package(s: Session, draft_id: UUID, member: CurrentMember, storage: LocalStorage,
                  today: date | None = None) -> tuple[bytes, str]:
    """回傳（zip 內容, 檔名）。有阻擋時 render_draft 丟 ExportBlocked，什麼都不產生。"""
    brief = render_draft(s, draft_id, member, today)
    title = safe_filename(export_brief_title(s, draft_id))
    exhibits = _cited_exhibits(s, [p.id for p in body_paragraphs(s, draft_id)])  # 與書狀「證物名稱及件數」同一份
    docs = [(ex, filename, s.get(Document, ex.document_id)) for ex, filename in exhibits]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"01_{title}.docx", brief)
        z.writestr("02_證物清單.docx", _exhibit_list(
            [(f"{ex.prefix}{ex.number}", filename, doc.page_count) for ex, filename, doc in docs]))
        for seq, (ex, filename, doc) in enumerate(docs, start=3):  # 序號不同，檔名不會互相覆蓋
            z.writestr(f"{seq:02d}_{safe_filename(f'{ex.prefix}{ex.number}_{filename}')}", storage.get(doc.storage_key))
        z.writestr("說明.txt", NOTE.encode("utf-8"))
    return buf.getvalue(), f"{title}_遞狀包.zip"
