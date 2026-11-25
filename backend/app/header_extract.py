"""從對方書狀開頭用規則讀出狀頭與對造姓名（設計 6.6；規則優先於 AI，這一步不呼叫 AI）。

原則：讀不出來就留空，不能讀錯。所以只讀書狀開頭（前兩頁、到「聲明」「事實及理由」這類標題為止），
當事人一定要是「原告／被告＋空白或冒號＋像名字的字串」，內文的「原告主張…」「被告應給付…」不算。
只填空的欄位；當事人只補「和我方角色相反」的人，我方這一邊（含共同被告）一律不自動加。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import CASE_LOCK
from app.ingest.service import document_page_texts
from app.models import Case, CaseParty, Document

HEADER_FIELDS = ("court", "case_number", "division", "cause")
_HEADING = re.compile(r"^[\s一二三四五六七八九十、．.（）()0-9]*(訴之聲明|答辯聲明|上訴聲明|聲明|事實及理由|理由|事實)\s*[:：]?\s*$")
_CJK = r"[一-鿿]"
_PERSON = rf"{_CJK}{{2,4}}"
_COMPANY = rf"[一-鿿A-Za-z0-9()]{{2,30}}?(?:股份有限公司|有限公司|公司)"
_STOP = r"(?=$|\s|[,，、;；]|住|設|身分證|訴訟代理人|法定代理人)"
_PARTY = re.compile(rf"^(原|被)\s*告(?:\s+|\s*:\s*)(?P<name>{_COMPANY}|{_PERSON}){_STOP}")
_CASE_NO = re.compile(r"(\d{2,3})\s*年度\s*(" + _CJK + r"{1,6})\s*字\s*第\s*(\d+)\s*號")
_DIVISION = re.compile(rf"(?<!{_CJK})({_CJK})股(?!{_CJK})")
_COURT = re.compile(rf"((?:臺灣|台灣){_CJK}{{2,8}}?法院|最高法院)")
_COURT_BEFORE_SEAL = re.compile(rf"({_CJK}{{2,12}}法院)(?:\s*(?:民事庭|家事法庭|民事簡易庭))?\s*公鑒")
_CAUSE = re.compile(rf"案由\s*:\s*({_CJK}{{2,20}})|為({_CJK}{{2,20}}?)事件")


@dataclass
class HeaderGuess:
    court: str | None = None
    case_number: str | None = None
    division: str | None = None
    cause: str | None = None
    plaintiffs: list[str] = field(default_factory=list)
    defendants: list[str] = field(default_factory=list)


def _clean(line: str) -> str:
    """全形空白、全形冒號等換成半形（NFKC），保留空白當作分隔；不像 normalize_text 會把空白拿掉。"""
    return unicodedata.normalize("NFKC", line).strip()


def header_block(pages: list[str]) -> str:
    """前兩頁從頭讀到第一個標題（聲明、事實及理由…）為止；沒有標題就用前兩頁全文。"""
    lines = []
    for line in "\n".join(pages[:2]).splitlines():
        if _HEADING.match(_clean(line)):
            break
        lines.append(line)
    return "\n".join(lines)


def read_header(text: str, *, full_text: str | None = None) -> HeaderGuess:
    """text 是書狀開頭（或整份前兩頁）；法院在開頭找不到時，再到 full_text（預設同 text）找「…法院　公鑒」。"""
    head = header_block([text])
    lines = [_clean(x) for x in head.splitlines()]
    g = HeaderGuess()
    for i, line in enumerate(lines):
        m = _PARTY.match(line)
        if m:
            (g.plaintiffs if m.group(1) == "原" else g.defendants).append(m.group("name"))
        if g.case_number is None and (n := _CASE_NO.search(line)):
            g.case_number = f"{n.group(1)}年度{n.group(2)}字第{n.group(3)}號"
            for near in (line[n.end():], lines[i + 1] if i + 1 < len(lines) else ""):
                if (d := _DIVISION.search(near)):
                    g.division = d.group(1)
                    break
    joined = " ".join(lines)
    if (c := _COURT.search(joined)):
        g.court = c.group(1)
    else:
        seal = _COURT_BEFORE_SEAL.search(_clean(" ".join((full_text or text).splitlines())))
        g.court = seal.group(1) if seal else None
    if (k := _CAUSE.search(joined)):
        g.cause = k.group(1) or k.group(2)
    g.plaintiffs = list(dict.fromkeys(g.plaintiffs))
    g.defendants = list(dict.fromkeys(g.defendants))
    return g


def _page_of(pages: list[tuple[int, str]], value: str) -> int:
    for no, text in pages:
        if value in _clean(" ".join(text.splitlines())):
            return no
    return pages[0][0] if pages else 1


def apply_header_guess(s: Session, case_id: UUID, document_id: UUID) -> list[str]:
    """把讀到的欄位填進空欄位、補上對造當事人；回傳這次填了哪些欄位或姓名。可重複呼叫（已填過的不再填）。"""
    doc = s.get(Document, document_id)
    if doc is None or doc.kind != "opponent_brief":
        return []
    pages = [(page.page_no, pt.text) for page, pt in document_page_texts(s, document_id) if pt is not None and pt.text]
    if not pages:
        return []
    texts = [t for _, t in pages[:2]]
    guess = read_header("\n".join(texts), full_text="\n".join(t for _, t in pages))
    case = s.get(Case, case_id, with_for_update=CASE_LOCK)
    filled: list[str] = []
    auto = dict(case.header_auto or {})
    for name in HEADER_FIELDS:
        value = getattr(guess, name)
        if value and not getattr(case, name):
            setattr(case, name, value)
            auto[name] = {"document_id": str(document_id), "page_no": _page_of(pages, value),
                          "verified_by": None, "verified_bar": None, "verified_at": None}
            filled.append(name)
    case.header_auto = auto
    our_role, their_role = ("原告", "被告") if case.our_side == "plaintiff" else ("被告", "原告")
    theirs = guess.defendants if their_role == "被告" else guess.plaintiffs
    parties = list(s.scalars(select(CaseParty).where(CaseParty.case_id == case_id)))
    known = {p.name for p in parties}
    seq = (s.scalar(select(func.max(CaseParty.seq)).where(CaseParty.case_id == case_id)) or 0)
    for name in theirs:
        if name in known:
            continue
        seq += 1
        s.add(CaseParty(firm_id=case.firm_id, case_id=case_id, seq=seq, name=name, role=their_role,
                        alignment="opposing", auto_document_id=document_id, auto_verified=False))
        filled.append(name)
    s.flush()
    if doc.filed_by_party_id is None and len(theirs) == 1:  # 書狀上只有一位對造：就是他遞的
        filer = s.scalars(select(CaseParty).where(CaseParty.case_id == case_id, CaseParty.name == theirs[0],
                                                  CaseParty.alignment != "ours")).first()
        if filer is not None:
            doc.filed_by_party_id = filer.id
    return filled


def header_unverified(s: Session, case_id: UUID) -> bool:
    """有自動帶入、還沒核對的狀頭欄位或對造當事人：匯出擋下（header_unverified）。"""
    case = s.get(Case, case_id)
    if any(v.get("verified_by") is None for v in (case.header_auto or {}).values()):
        return True
    return s.scalar(select(CaseParty.id).where(CaseParty.case_id == case_id, CaseParty.auto_document_id.is_not(None),
                                               CaseParty.auto_verified.is_(False)).limit(1)) is not None


def verify_header(s: Session, case_id: UUID, member_id: UUID, bar_number: str | None,
                  fields: tuple[str, ...] | None = None) -> None:
    """律師核對：fields 為 None 時確認目前全部未核對的欄位與自動帶入的當事人。"""
    case = s.get(Case, case_id, with_for_update=CASE_LOCK)
    now = datetime.now(timezone.utc).isoformat()
    auto = {}
    for name, entry in (case.header_auto or {}).items():
        if entry.get("verified_by") is None and (fields is None or name in fields):
            entry = {**entry, "verified_by": str(member_id), "verified_bar": bar_number, "verified_at": now}
        auto[name] = entry
    case.header_auto = auto
    if fields is None:
        for p in s.scalars(select(CaseParty).where(CaseParty.case_id == case_id, CaseParty.auto_document_id.is_not(None))):
            p.auto_verified = True
