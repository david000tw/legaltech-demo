"""律師貼司法院判決網址 → 系統自己向司法院下載 PDF → 切段後加入判決庫（追加範圍，設計 5.9）。

判決只能來自司法院：只收 judgment.judicial.gov.tw 的民事、家事判決網址；下載不跟隨轉址、
不偽裝瀏覽器，標明程式名稱。寫入只透過 corpus_session（corpus_writer 帳號）。
"""
from __future__ import annotations

import http.client
import io
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.corpus.splitter import split_judgment, split_problems
from app.models import Judgment, JudgmentPassage

HOST = "judgment.judicial.gov.tw"
USER_AGENT = "legaltech-judgment-import/0.1"
MAX_PDF_BYTES = 20_000_000
TIMEOUT_SECONDS = 30
BAD_URL = "只接受司法院判決書系統的民事、家事判決網址"
FETCH_FAILED = "司法院網站沒有回傳判決 PDF，請確認網址或稍後再試"
UNREADABLE = "無法讀取這份判決 PDF 的文字，暫時不能加入"
NO_HEADER = "無法從判決內容判讀法院與字號，暫時不能加入"
CLASH = "判決庫已有同字號、但來源不同的判決，請聯絡管理員"
UNSPLITTABLE = "這份判決無法自動切成段落，暫時不能加入；請改用其他判決"

_ID = re.compile(r"([A-Z]{3}V),(\d{1,3}),([^,\s]{1,10}),(\d{1,7}),(\d{8}),(\d{1,3})")
_FILES = re.compile(r"/FILES/([A-Z]{4})/([^/]+)\.pdf")
_HEADER = re.compile(r"(?P<court>\S+?法院(?:\S{1,6}?分院)?)(?:\S{1,8}?簡易庭)?\s*"
                     r"(?P<kind>(?:民事|家事)\S{0,4}?(?:判決|裁定))")
_LEVEL = {"S": "supreme", "H": "high", "E": "summary"}


class ImportRejected(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message  # 給律師看的中文


@dataclass(frozen=True)
class JudgmentRef:
    court_code: str
    year: int
    word: str
    number: int
    decided_on: date
    seq: int

    @property
    def key(self) -> str:
        return f"{self.court_code},{self.year},{self.word},{self.number},{self.decided_on:%Y%m%d},{self.seq}"


def parse_judgment_url(url: str) -> JudgmentRef:
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise ImportRejected(BAD_URL)
    if u.scheme != "https" or u.hostname != HOST or u.username or u.password or port is not None:
        raise ImportRejected(BAD_URL)
    path = unquote(u.path)
    files = _FILES.fullmatch(path)
    if files:
        raw = f"{files.group(1)},{files.group(2)}"
    elif path.lower() == "/fjud/data.aspx":
        ids = parse_qs(u.query).get("id")
        if not ids:
            raise ImportRejected(BAD_URL)
        raw = ids[0]
    else:
        raise ImportRejected(BAD_URL)
    m = _ID.fullmatch(raw.strip())
    if not m:
        raise ImportRejected(BAD_URL)
    try:
        decided = datetime.strptime(m.group(5), "%Y%m%d").date()
    except ValueError:
        raise ImportRejected(BAD_URL)
    return JudgmentRef(m.group(1), int(m.group(2)), m.group(3), int(m.group(4)), decided, int(m.group(6)))


def pdf_url(ref: JudgmentRef) -> str:
    return (f"https://{HOST}/FILES/{ref.court_code}/{ref.year},{quote(ref.word, safe='')},{ref.number},"
            f"{ref.decided_on:%Y%m%d},{ref.seq}.pdf")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何轉址都不跟：回 None 讓 urllib 把 3xx 當錯誤丟出。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(*handlers) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect, *handlers)


def fetch_pdf(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener().open(req, timeout=TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                raise ImportRejected(FETCH_FAILED)
            data = resp.read(MAX_PDF_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):  # 後者：傳輸中斷、狀態列壞掉
        raise ImportRejected(FETCH_FAILED)
    if len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF"):
        raise ImportRejected(FETCH_FAILED)
    return data


def pdf_text(data: bytes) -> str:
    """與種子建檔相同的抽字方式（scripts/build_seed_judgment.py）：pypdf 逐頁抽字、以換行串接。"""
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001 - pypdf 對壞檔丟的例外種類很多，一律當成讀不到
        raise ImportRejected(UNREADABLE)


def _header(full_text: str) -> tuple[str, str]:
    m = _HEADER.search(full_text[:300])
    if not m:
        raise ImportRejected(NO_HEADER)
    return m.group("court"), m.group("kind")


def citation_from_text(full_text: str, ref: JudgmentRef) -> str:
    """引用字號：法院（保留分院、去掉簡易庭，與種子一致）＋網址裡的年度字號＋判決種類。"""
    court, kind = _header(full_text)
    return f"{court}{ref.year}年度{ref.word}字第{ref.number}號{kind}"


def _find_by_key(s: Session, key: str) -> Judgment | None:
    return s.scalars(select(Judgment).where(Judgment.source_key == key)).one_or_none()


def import_judgment(corpus_s: Session, ref: JudgmentRef, full_text: str, case_type: str) -> tuple[Judgment, bool]:
    """回傳（判決, 是否新加入）。同一件（同來源代碼）已在判決庫就直接回傳既有的。"""
    existing = _find_by_key(corpus_s, ref.key)
    if existing is not None:
        return existing, False
    citation = citation_from_text(full_text, ref)
    if corpus_s.scalar(select(Judgment.id).where(Judgment.citation == citation)) is not None:
        raise ImportRejected(CLASH)
    passages = split_judgment(full_text)
    if split_problems(passages, full_text, False):
        raise ImportRejected(UNSPLITTABLE)
    court, _ = _header(full_text)
    j = Judgment(citation=citation, court=court, court_level=_LEVEL.get(ref.court_code[2], "district"),
                 decided_on=ref.decided_on, source_url=pdf_url(ref), source_key=ref.key, full_text=full_text,
                 status="active", curated=False, case_types=[case_type])
    try:
        with corpus_s.begin_nested():
            corpus_s.add(j)
            corpus_s.flush()
            corpus_s.add_all(JudgmentPassage(judgment_id=j.id, seq=seq, passage_type=kind, text=text, status="active")
                             for seq, (kind, text) in enumerate(passages, start=1))
            corpus_s.flush()
    except IntegrityError:
        # 兩人同時加入同一件：另一邊先寫入了，回傳那一筆
        existing = _find_by_key(corpus_s, ref.key)
        if existing is None:
            raise ImportRejected(CLASH)
        return existing, False
    return j, True
