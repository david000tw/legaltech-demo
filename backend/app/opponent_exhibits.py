"""對方書狀附的證物（對方證物計畫）：用規則找出「原證3 是第幾頁到第幾頁」，讓草稿能引用「原告所提原證3第1頁」。

原則（規則優先於 AI；寧可不找，不可找錯）：
- 只看簽名頁（第一個「公鑒／公鑑」的頁；簽名區塊跨頁時是含「具狀人」的下一頁）之後的頁。
- 某頁前 3 個非空白行裡，整行是標示（「原證3」「被證二號」「原證3：收據」）而且只有這一個標示，這頁就是該證物的第一頁。
- 看不準的頁（沒有文字、像標示但讀不懂，例如「原證2-1」「附件1」、一頁兩個標示）不屬於任何證物，也結束目前的證物。
- 沒有任何像標示文字的頁，接在目前證物後面（續頁）。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import CaseParty, Document, OpponentExhibit, Page, PageText

# 依長度由長到短：「被上證」要先於「上證」、「原告證」要先於「原證」比對
PREFIXES = ("被上證", "原告證", "被告證", "原證", "被證", "上證", "聲證", "相證")
_CN_DIGITS = "一二三四五六七八九十"
_LABEL = re.compile(rf"^({'|'.join(PREFIXES)})([0-9]+|[{_CN_DIGITS}]+)號?(?:[:(].*)?$")
# 「像標示」：寬鬆版，用來判斷「這頁有標示但讀不懂」（例如原證2-1、【原證1】、附件1、甲證3）
_LIKE = re.compile(rf"^[【\[(]?(?:{'|'.join(PREFIXES)}|告證|甲證|乙證|丙證|證物|附件|附表)[0-9{_CN_DIGITS}]")
_SEAL = ("公鑒", "公鑑")  # 「鑑」是常見的異體寫法，NFKC 不會合併
_SIGNER = ("具狀人", "撰狀人")
_CN = {c: i for i, c in enumerate("一二三四五六七八九", start=1)}


@dataclass(frozen=True)
class FoundExhibit:
    prefix: str
    number: int
    first_page: int
    last_page: int


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _number(raw: str) -> int | None:
    """阿拉伯數字或中文一～九十九。"""
    if raw.isdigit():
        n = int(raw)
        return n if n > 0 else None
    if raw.count("十") > 1:
        return None
    if "十" in raw:
        tens, _, ones = raw.partition("十")
        t = _CN.get(tens) if tens else 1
        o = _CN.get(ones) if ones else 0
        return None if t is None or o is None else t * 10 + o
    return _CN.get(raw) if len(raw) == 1 else None


def parse_label(line: str) -> tuple[str, int] | None:
    """整行就是標示才回 (前綴, 號碼)；句子裡提到的「此有原證3可證」不算。"""
    m = _LABEL.match(_compact(line))
    if m is None:
        return None
    n = _number(m.group(2))
    return (m.group(1), n) if n is not None else None


def looks_like_label(line: str) -> bool:
    return bool(_LIKE.match(_compact(line)))


def _signature_index(pages: list[tuple[int, str | None]]) -> int | None:
    for keys in (_SEAL, _SIGNER):
        for i, (_, text) in enumerate(pages):
            if text is None:
                return None  # 簽名頁之前有頁還沒文字：不知道簽名頁在哪，整份不找
            if any(k in _compact(text) for k in keys):
                return i
    return None


def find_exhibits(pages: list[tuple[int, str | None]]) -> list[FoundExhibit]:
    """pages：(頁碼, 文字或 None)，頁碼遞增。"""
    idx = _signature_index(pages)
    if idx is None:
        return []
    text = pages[idx][1] or ""
    if not any(k in _compact(text) for k in _SIGNER) and idx + 1 < len(pages):
        nxt = pages[idx + 1][1]
        if nxt is not None and any(k in _compact(nxt) for k in _SIGNER):
            idx += 1  # 簽名區塊跨頁：證物清單尾巴與具狀人在下一頁
    out: list[FoundExhibit] = []
    cur: list | None = None  # [前綴, 號碼, 起始頁, 結束頁]

    def close() -> None:
        nonlocal cur
        if cur is not None:
            out.append(FoundExhibit(*cur))
        cur = None

    for no, text in pages[idx + 1:]:
        if text is None:
            close()
            continue
        lines = [ln for ln in text.splitlines() if ln.strip()]
        head = {lab for lab in (parse_label(ln) for ln in lines[:3]) if lab is not None}
        others = [ln for ln in lines if looks_like_label(ln) and parse_label(ln) not in head]
        if len(head) == 1 and not others:
            prefix, number = next(iter(head))
            if cur is not None and (cur[0], cur[1]) == (prefix, number):
                cur[3] = no
            else:
                close()
                cur = [prefix, number, no, no]
        elif head or others:
            close()  # 有標示但看不準：這頁不屬於任何證物
        elif cur is not None:
            cur[3] = no
    close()
    return out


# ---- 存起來、適時重找（Task 2） ----

def refresh_opponent_exhibits(s: Session, document_id: UUID) -> list[OpponentExhibit]:
    """依目前頁文字整份重算。先鎖文件列：背景重找與起草前重找同時跑時，不會寫出重複的列。"""
    from app.ingest.service import document_page_texts
    doc = s.get(Document, document_id, with_for_update=True)
    if doc is None or doc.kind != "opponent_brief":
        return []
    before = cited_opponent_labels(s, doc.case_id)
    pages = [(page.page_no, pt.text if pt is not None else None) for page, pt in document_page_texts(s, document_id)]
    found = find_exhibits(pages)
    s.execute(delete(OpponentExhibit).where(OpponentExhibit.document_id == document_id))
    rows = [OpponentExhibit(firm_id=doc.firm_id, document_id=doc.id, prefix=f.prefix, number=f.number,
                            first_page_no=f.first_page, last_page_no=f.last_page) for f in found]
    s.add_all(rows)
    s.flush()
    reset_changed_labels(s, doc.case_id, before)
    return rows


def refresh_case_opponent_exhibits(s: Session, case_id: UUID) -> None:
    """本案所有對方書狀（不依檔案內容去重：複製輪次的文件也要有自己的列）；依 id 順序上鎖。"""
    for doc_id in s.scalars(select(Document.id).where(Document.case_id == case_id, Document.kind == "opponent_brief")
                            .order_by(Document.id)).all():
        refresh_opponent_exhibits(s, doc_id)


def opponent_exhibit_for_page(s: Session, page_id: UUID) -> tuple[OpponentExhibit, int] | None:
    """這一頁屬於哪一件對方證物，以及是該證物的第幾頁。"""
    page = s.get(Page, page_id)
    if page is None:
        return None
    ex = s.scalars(select(OpponentExhibit).where(OpponentExhibit.document_id == page.document_id,
                                                 OpponentExhibit.first_page_no <= page.page_no,
                                                 OpponentExhibit.last_page_no >= page.page_no)).first()
    return (ex, page.page_no - ex.first_page_no + 1) if ex is not None else None


# ---- 標籤與可引用頁（Task 3） ----

# 沒有遞狀人時由前綴推角色
_PREFIX_ROLE = {"原證": "原告", "原告證": "原告", "被證": "被告", "被告證": "被告", "上證": "上訴人",
                "被上證": "被上訴人", "聲證": "聲請人", "相證": "相對人"}


def _collides(s: Session, ex: OpponentExhibit, doc: Document) -> bool:
    """同一案件另一份「不同檔案」的對方書狀也有同一個編號（複製輪次的同一份檔案不算）。"""
    return s.scalar(select(OpponentExhibit.id).join(Document, Document.id == OpponentExhibit.document_id)
                    .where(Document.case_id == doc.case_id, Document.sha256 != doc.sha256,
                           OpponentExhibit.prefix == ex.prefix, OpponentExhibit.number == ex.number)
                    .limit(1)) is not None


def opponent_label(s: Session, ex: OpponentExhibit, n: int) -> str:
    """例：「原告所提原證3第1頁」；撞號時加遞狀人姓名（「被告王小華所提被證1第1頁」）或檔名。"""
    doc = s.get(Document, ex.document_id)
    filer = s.get(CaseParty, doc.filed_by_party_id) if doc.filed_by_party_id else None
    who = filer.role if filer is not None else _PREFIX_ROLE.get(ex.prefix, "對造")
    tail = ""
    if _collides(s, ex, doc):
        if filer is not None:
            who = f"{filer.role}{filer.name}"
        else:
            tail = f"（{doc.filename}）"
    return f"{who}所提{ex.prefix}{ex.number}{tail}第{n}頁"


def opponent_exhibit_pages(s: Session, case_id: UUID) -> list[PageText]:
    """本案對方證物頁的目前文字版本（同一份檔案只算一次，依上傳順序、頁碼）；沒有文字的頁不列。"""
    from app.ingest.service import document_page_texts
    from app.sources import opponent_docs
    out: list[PageText] = []
    for _, doc in opponent_docs(s, case_id):
        rows = s.scalars(select(OpponentExhibit).where(OpponentExhibit.document_id == doc.id)).all()
        if not rows:
            continue
        for page, pt in document_page_texts(s, doc.id):
            if pt is not None and any(r.first_page_no <= page.page_no <= r.last_page_no for r in rows):
                out.append(pt)
    return out


# ---- 編號變了，已審段落退回（Task 4） ----

LABEL_CHANGED = "對方證物編號已變更"


def cited_opponent_labels(s: Session, case_id: UUID) -> dict[UUID, str]:
    """本案段落引用到的對方書狀頁：page_text_id → 目前顯示的標籤。"""
    from app.exhibit_labels import evidence_label
    from app.models import Citation
    ids = s.scalars(select(Citation.page_text_id).join(PageText, PageText.id == Citation.page_text_id)
                    .join(Page, Page.id == PageText.page_id).join(Document, Document.id == Page.document_id)
                    .where(Document.case_id == case_id, Document.kind == "opponent_brief",
                           Citation.kind == "evidence").distinct()).all()
    return {pt_id: evidence_label(s, pt_id) for pt_id in ids}


def reset_changed_labels(s: Session, case_id: UUID, before: dict[UUID, str]) -> list[UUID]:
    """標籤變了（含不再屬於任何證物）的引用：段落退回未審並提示。鎖法同 renumber_exhibit（依 id 排序）。"""
    if not before:
        return []
    after = cited_opponent_labels(s, case_id)
    changed = [pt_id for pt_id, label in before.items() if after.get(pt_id) != label]
    if not changed:
        return []
    from app.models import Citation, Paragraph
    from app.pipeline.drafting import reset_review
    pids = s.scalars(select(Citation.paragraph_id).where(Citation.page_text_id.in_(changed)).distinct()).all()
    locked = s.scalars(select(Paragraph).where(Paragraph.id.in_(pids)).order_by(Paragraph.id)
                       .with_for_update().execution_options(populate_existing=True)).all()
    for para in locked:
        reset_review(para)
        para.source_notice = LABEL_CHANGED
    s.flush()
    return [para.id for para in locked]
