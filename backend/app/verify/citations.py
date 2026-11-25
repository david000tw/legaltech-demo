"""引用驗證第 1～3 層。

第 1 層：段落只能用 {{cite:代碼}}／{{quote:代碼}} 引用候選清單內的代碼。
第 2 層：標題與內文不得自行寫字號、條號、原證號、頁碼（阿拉伯或中文數字）；
        自行加引號、15 字以上的文字必須逐字出自本輪對造書狀。
第 3 層：引文逐字出自來源、長度 ≥15、判決只能引法院判斷段、來源未被撤下。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Candidate, Citation, Claim, Document, Draft, Judgment, JudgmentPassage, Page, PageText, Paragraph, Round,
    StatuteArticle,
)
from app.ingest.service import current_page_text
from app.numbering import strip_heading_number
from app.pipeline.claims import opponent_pages
from app.amounts import parse_amounts
from app.statements import (
    AMOUNT_MARKER, amount_marker_allowed, confirmed_requests, draft_reasons, round_of, round_total,
    statement_of, statement_reasons,
)
from app.ocr.state import UNREADABLE, needs_check
from app.sources import page_label
from app.text_norm import find_quote, normalize_text, quote_length

MARKER_RE = re.compile(r"\{\{(cite|quote):([A-Z]\d+)\}\}")
# Word（XML）不接受的字元：匯出時會讓 python-docx 丟例外
INVALID_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")
NUM = r"[〇零一二三四五六七八九十百千\d]+"
NUMF = r"[〇零一二三四五六七八九十百千壹貳參叁肆伍陸柒捌玖拾佰仟\d]+"  # 含大寫數字（字號、條號用）
_DASH = r"[-‐‑–—]"
_CASE_NUMBER_PATTERNS = [  # 判決字號、簡寫字號、大法官解釋、決議
    re.compile(rf"{NUMF}\s*年度?\s*[一-鿿]{{1,6}}?\s*字\s*第?\s*{NUMF}\s*號"),  # 含憲判字、判例；「第」可省
    re.compile(rf"{NUM}\s*[台臺][一-鿿]{{1,3}}\s*{NUM}"),  # 簡寫字號：110台上1234
    re.compile(rf"{NUMF}\s*年度?\s*[台臺][一-鿿]{{1,3}}\s*字?\s*第?\s*{NUMF}\s*號"),  # 99年台上1號
    re.compile(rf"釋字\s*第?\s*{NUMF}\s*號?"),
    re.compile(rf"(?:大法官|司法院)[一-鿿]{{0,6}}?\s*第\s*{NUMF}\s*號"),
    re.compile(rf"解釋\s*第?\s*{NUMF}\s*號"),
    re.compile(rf"第\s*{NUMF}\s*號\s*解釋"),
    re.compile(rf"{NUM}\s*年度?\s*第\s*{NUM}\s*次\s*民事庭(?:會議)?決議"),
]
_UNBOUND_PATTERNS = _CASE_NUMBER_PATTERNS + [
    re.compile(rf"(?:[原被上甲乙丙丁]證|證物)\s*物?\s*(?:編號)?\s*{NUM}"),
    re.compile(rf"第\s*{NUM}\s*頁"),
    re.compile(rf"第\s*{NUMF}(?:\s*[、，,及與和]\s*第?\s*{NUMF})*\s*條(?:\s*之\s*{NUMF})?"),  # 第184、185條
    re.compile(rf"第\s*{NUMF}\s*{_DASH}\s*{NUMF}\s*條"),  # 第191-2條
    re.compile(rf"(?:法|規則|條例|辦法)\s*{NUMF}\s*(?:{_DASH}\s*{NUMF}\s*)?條"),  # 省略「第」：民法184條
    re.compile(rf"§\s*{NUM}"),
]
# 法條標記後面自己接「之N」，展開後就變成另一條沒驗證過的條文（第184條→第184條之1）；「第N項」仍在同一條內，允許
_STATUTE_SUFFIX = re.compile(rf"\{{\{{(?:cite|quote):(S\d+)\}}\}}\s*之\s*({NUMF})")
# 法院判斷段裡轉述的當事人主張：從「原告雖主張」之類開始，優先到「等語／云云／等情／置辯」（一定長度內），
# 找不到才到第一個句號為止。
# 角色與動詞之間可夾 1～12 字的姓名、代號或稱謂（「被告甲○○雖辯稱」「被告甲○○、乙○○雖抗辯」
# 「反訴原告（即被告）雖主張」「原告即被繼承人之長子甲○○主張」）；但不得以指示詞開頭，
# 否則「原告前開主張屬實」「原告之主張即非可採」這類法院結論也會被當成轉述。
_ROLE = r"(?:原告|被告|上訴人|被上訴人|聲請人|相對人|兩造|參加人|債務人|債權人)"
_PARTY_START = re.compile(_ROLE +
                          r"(?:(?!前|上開|上述|上揭|此|其|所|之|該|本院|本件)"
                          r"(?:[^,，。;；:：「」『』()（）]|[（(]即[^)）]{1,8}[)）]){1,12}?)?"
                          r"(?:雖|固|另|復|則|亦|並|又|均|僅)?"
                          r"(?:起訴主張|主張|辯稱|辯解|抗辯|辯以|陳稱|泛稱|指稱|則以|雖以|固以|答辯|置辯|否認|陳述|謂|稱)")
_PARTY_TERMINATOR = re.compile(r"等語|云云|等情|置辯")
_COURT_TURN = re.compile(r"然查|惟查|經查|次查|又查|本院|按")  # 轉入法院自己的論述
# 反推規則用：同一句裡的角色詞、讓步語（雖以…）與轉折語（然／惟／但）
_ROLE_RE = re.compile(_ROLE)
_CONCESSIVE = re.compile(r"(?:雖|固)(?:以|主張|稱|辯|抗辯|執|指)")
_ADVERSATIVE = re.compile(r"然|惟|但")
_QUOTED_END = re.compile(r"等語|云云")  # 反推只認這兩種；「等情」常是法院整理證據認定的事實（「…等情，有…可稽」）
PARTY_SPAN_LIMIT = 400
_OPEN_Q = "「『“〝﹁‘\""
_CLOSE_Q = "」』”〞﹂’\""
_ANY_QUOTE = re.compile(f"[{_OPEN_Q}{_CLOSE_Q}]")
_SENTENCE_END = re.compile(r"[。！？!?\n]")
QUOTE_GROUP_GAP = 3  # 引號段之間只隔幾個字時，視為同一段引文（防止拆成兩段各 14 字）
MIN_QUOTE = 15
KIND_BY_PREFIX = {"E": "evidence", "J": "judgment", "S": "statute"}
# 同一段落對同一代碼給了不同引文：一個代碼只存一筆引文（標記只帶代碼），多出來的那筆不會被展開也不會被驗證。
# 這個標記記在引用的 verify_reasons 上，每次重新驗證都保留，直到段落被改寫（改寫會重建引用）。
QUOTE_CONFLICT = "quote_conflict"


def _scan_form(text: str) -> str:
    """去掉合法的引用標記後，做 NFKC 並刪除格式字元（零寬空白等），供掃描用。"""
    t = unicodedata.normalize("NFKC", MARKER_RE.sub("", text or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Cf")


def has_malformed_marker(text: str) -> bool:
    """合法標記以外，只要還有大括號（含全形）就是格式錯誤的標記。"""
    t = _scan_form(text)
    return "{" in t or "}" in t


def scan_unbound_references(text: str) -> list[str]:
    raw = unicodedata.normalize("NFKC", text or "")
    hits: list[tuple[int, str]] = [(-1, f"{m.group(1)}之{m.group(2)}") for m in _STATUTE_SUFFIX.finditer(raw)]
    t = _scan_form(text)
    for pat in _UNBOUND_PATTERNS:
        hits.extend((m.start(), m.group(0)) for m in pat.finditer(t))
    seen, out = set(), []
    for _, h in sorted(hits):
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


_CLAUSE_BREAK = set("，。；：、（）()「」『』,;:\n 　")


def scan_case_numbers_with_context(text: str, reach: int = 20) -> list[tuple[str, str]]:
    """文字裡直接寫的判決字號、大法官解釋、決議（不含條號、證物號、頁碼），依出現順序、不重複，
    各附字號前面同一個子句裡的文字（最多 reach 字）：
    「可參臺灣臺北地方法院113年度…」→（「113年度…」,「可參臺灣臺北地方法院113年度…」），對回候選時才查得到法院。"""
    t = _scan_form(text)
    hits = sorted((m.start(), m.group(0)) for pat in _CASE_NUMBER_PATTERNS for m in pat.finditer(t))
    out: dict[str, str] = {}
    for start, hit in hits:
        i = start
        while i > max(0, start - reach) and t[i - 1] not in _CLAUSE_BREAK:
            i -= 1
        out.setdefault(hit, t[i:start] + hit)
    return list(out.items())


def _outer_quotes(t: str) -> list[tuple[int, int, int]]:
    """最外層的引號段：(內容起點, 內容終點, 該段結束位置)。內層引號算在外層裡；沒關上的引號延伸到句末。"""
    out = []
    i, n = 0, len(t)
    while i < n:
        if t[i] not in _OPEN_Q:
            i += 1
            continue
        stack, j = [t[i]], i + 1
        while j < n and stack:
            c = t[j]
            if c == '"' and stack[-1] == '"':
                stack.pop()
            elif c in _OPEN_Q:
                stack.append(c)
            elif c in _CLOSE_Q:
                stack.pop()
            j += 1
        if stack:
            m = _SENTENCE_END.search(t, i + 1)
            end = m.start() if m else n
            out.append((i + 1, end, end))
            i = max(end, i + 1)
        else:
            out.append((i + 1, j - 1, j))
            i = j
    return out


# 用段落編號參照其他段落（「如前㈡所述」「見前述（二）」）：刪段後系統重新編號，參照就錯了。
# 在 NFKC 之後比對：㈠ 會變成 (一)、（十一）會變成 (十一)。
# 內文出現系統編號格式一律標記，只排除：附表、原證等編號（「附表(一)」），以及列舉（「：(一)…；(二)…」
# 「包括(一)…、(二)…」）。
_NUM = re.compile(r"\([一二三四五六七八九十]{1,3}\)")
_NUM_OWNER = re.compile(r"(?:表|附件|原證|被證|證物|附圖)$")
_LIST_BEFORE = tuple(":：;；。,，、")
# 「包括(一)…、(二)…」：同一句後面還有「、(二)」才是列舉；「損害包括(一)所述醫療費用」是段落參照
_LIST_AFTER = re.compile(r"[^。；;]*?、\s*\([一二三四五六七八九十]{1,3}\)")


def scan_numbered_cross_references(text: str) -> list[str]:
    t = _scan_form(text)
    out = []
    for m in _NUM.finditer(t):
        before = t[:m.start()].rstrip()
        if not before or before.endswith(_LIST_BEFORE) or _NUM_OWNER.search(before):
            continue
        if before.endswith("包括") and _LIST_AFTER.match(t, m.end()):
            continue
        out.append(t[max(0, m.start() - 3):m.end() + 2])
    return out


def _quote_groups(text: str) -> list[list[tuple[str, str]]]:
    """段落裡自行加引號、合計 15 字以上的引文段；每段是 [(原樣, 去掉內層引號)]。"""
    t = _scan_form(text)
    groups: list[list[tuple[int, int, int]]] = []
    for span in _outer_quotes(t):
        if groups and len(normalize_text(t[groups[-1][-1][2]:span[0] - 1])) <= QUOTE_GROUP_GAP:
            groups[-1].append(span)
        else:
            groups.append([span])
    out = []
    for g in groups:
        parts = [(t[a:b], _ANY_QUOTE.sub("", t[a:b])) for a, b, _ in g]
        if sum(quote_length(bare) for _, bare in parts) >= MIN_QUOTE:
            out.append(parts)
    return out


def _quote_head(parts: list[tuple[str, str]]) -> str:
    return normalize_text("".join(bare for _, bare in parts))[:10]


def _found_in(parts: list[tuple[str, str]], texts: list[str]) -> bool:
    return all(any(find_quote(op, raw) or find_quote(op, bare) for op in texts)
               for raw, bare in parts if quote_length(bare) > 0)


def scan_inline_quotes(text: str, opponent_texts: list[str]) -> list[str]:
    return [_quote_head(parts) for parts in _quote_groups(text) if not _found_in(parts, opponent_texts)]


def scan_inline_quotes_split(text: str, verified_texts: list[str],
                             unverified_texts: list[str]) -> tuple[list[str], list[str]]:
    """（在任何對造頁都找不到的引文開頭, 只在還沒對照原圖的辨識頁找得到的引文開頭）。"""
    missing, unverified = [], []
    for parts in _quote_groups(text):
        if _found_in(parts, verified_texts):
            continue
        if _found_in(parts, verified_texts + unverified_texts):
            unverified.append(_quote_head(parts))
        else:
            missing.append(_quote_head(parts))
    return missing, unverified


def _round_id_for(s: Session, p: Paragraph) -> UUID:
    return s.get(Draft, p.draft_id).round_id


def _candidate(s: Session, round_id: UUID, code: str) -> Candidate | None:
    return s.scalars(select(Candidate).where(Candidate.round_id == round_id, Candidate.code == code)).first()


def attach_citations(s: Session, p: Paragraph, items: list[tuple[str, str]]) -> list[Citation]:
    """依候選清單把 (代碼, 引文) 綁到段落；清單外的代碼也建立，但沒有來源，驗證時會失敗。"""
    round_id = _round_id_for(s, p)
    out, seen = [], {}
    for code, quote in items:
        code = str(code)
        if code in seen:
            if normalize_text(seen[code].quote) != normalize_text(str(quote or "")):
                seen[code].verify_reasons = [QUOTE_CONFLICT]  # 不能默默丟掉第二筆：留記號，驗證時擋下
            continue
        cand = _candidate(s, round_id, code)
        c = Citation(firm_id=p.firm_id, paragraph_id=p.id, code=code,
                     kind=cand.kind if cand else KIND_BY_PREFIX.get(code[:1], "evidence"),
                     quote=str(quote or ""), verify_status="failed", verify_reasons=[],
                     page_text_id=cand.page_text_id if cand else None,
                     judgment_passage_id=cand.judgment_passage_id if cand else None,
                     statute_article_id=cand.statute_article_id if cand else None)
        s.add(c)
        seen[code] = c
        out.append(c)
    s.flush()
    return out


def _in_party_claim(passage_text: str, quote: str) -> bool:
    norm, q = normalize_text(passage_text), normalize_text(quote)
    pos = norm.find(q)
    if not q or pos < 0:
        return False
    for m in _PARTY_START.finditer(norm):
        term = _PARTY_TERMINATOR.search(norm, m.end(), m.end() + PARTY_SPAN_LIMIT)
        if term and not _COURT_TURN.search(norm, m.end(), term.start()):
            end = term.end()
        else:
            period = norm.find("。", m.end())
            end = period + 1 if period >= 0 else len(norm)
        if m.start() < pos + len(q) and pos < end:
            return True
    return _restated_in_sentence(norm, pos, pos + len(q))


def _restated_in_sentence(norm: str, qs: int, qe: int) -> bool:
    """角色詞與動詞被逗號隔開時的反推規則（只看引文所在的那一句）：
    1. 句中引文之前（含引文）有角色詞，引文之後以「等語／云云」作結 → 轉述；
    2. 句中角色詞之後有「雖以／雖主張…」，引文落在它之後、下一個「然／惟／但」之前 → 轉述。"""
    s0 = norm.rfind("。", 0, qs) + 1
    s1 = norm.find("。", qe)
    s1 = len(norm) if s1 < 0 else s1
    role = _ROLE_RE.search(norm, s0, qe)
    if role is None:
        return False
    if _QUOTED_END.search(norm, qs, s1):
        return True
    concession = _CONCESSIVE.search(norm, role.end(), qs + 1)
    return concession is not None and _ADVERSATIVE.search(norm, qe, s1) is not None


def _contains_case_number(quote: str) -> bool:
    t = _scan_form(quote)
    return any(p.search(t) for p in _CASE_NUMBER_PATTERNS)


def _quote_reasons(source: str, quote: str) -> list[str]:
    reasons = []
    if find_quote(source, quote) is None:
        reasons.append("quote_not_found")
    if quote_length(quote) < MIN_QUOTE:
        reasons.append("quote_too_short")
    return reasons


def verify_citation(s: Session, c: Citation) -> list[str]:
    p = s.get(Paragraph, c.paragraph_id)
    cand = _candidate(s, _round_id_for(s, p), c.code)
    target_ok = cand is not None and cand.kind == c.kind and (
        (c.kind == "evidence" and c.page_text_id and c.page_text_id == cand.page_text_id)
        or (c.kind == "judgment" and c.judgment_passage_id and c.judgment_passage_id == cand.judgment_passage_id)
        or (c.kind == "statute" and c.statute_article_id and c.statute_article_id == cand.statute_article_id))
    if not target_ok:
        reasons = ["unknown_code"]
    elif c.kind == "evidence":
        pt = s.get(PageText, c.page_text_id)
        reasons = _quote_reasons(pt.text, c.quote)
        page = s.get(Page, pt.page_id)
        if s.get(Document, page.document_id).kind == "opponent_brief":
            from app.opponent_exhibits import opponent_exhibit_for_page
            if opponent_exhibit_for_page(s, page.id) is None:
                reasons.append("opponent_page_not_exhibit")  # 對方書狀本文或沒有編號的附件：不能引用
        if UNREADABLE in (c.quote or ""):  # 辨識時看不清楚的字：要先修正辨識文字，不能寫進書狀
            reasons.append("quote_unreadable")
        latest = current_page_text(s, pt.page_id)
        if latest is not None and latest.id != pt.id:  # 該頁文字已校正成新版本，舊版引用要重新確認
            reasons.append("source_superseded")
        if _contains_case_number(c.quote):  # 卷證裡埋的字號逐字引用後，展開的書狀仍會出現字號
            reasons.append("quote_contains_case_number")
    elif c.kind == "judgment":
        passage = s.get(JudgmentPassage, c.judgment_passage_id)
        judgment = s.get(Judgment, passage.judgment_id)
        reasons = []
        if judgment.status != "active" or passage.status != "active":
            reasons.append("source_withdrawn")
        if passage.passage_type != "court_reasoning":
            reasons.append("not_court_reasoning")
        reasons += _quote_reasons(passage.text, c.quote)
        if passage.passage_type == "court_reasoning" and _in_party_claim(passage.text, c.quote):
            reasons.append("possible_party_claim")
    else:
        article = s.get(StatuteArticle, c.statute_article_id)
        if article is None:
            reasons = ["statute_missing"]
        elif c.quote.strip() and find_quote(article.text, c.quote) is None:
            reasons = ["quote_not_found"]
        else:
            reasons = []
    if QUOTE_CONFLICT in (c.verify_reasons or []):
        reasons.append(QUOTE_CONFLICT)
    c.verify_reasons = reasons
    if not reasons:
        c.verify_status = "ok"
    elif reasons == ["possible_party_claim"]:  # 只有這一項：可能是法院結論，交律師讀原文後逐筆確認
        c.verify_status = "warn"
    else:
        c.verify_status = "failed"
    return reasons


def paragraph_citations(s: Session, paragraph_id: UUID) -> list[Citation]:
    return list(s.scalars(select(Citation).where(Citation.paragraph_id == paragraph_id).order_by(Citation.code)))


def _amounts_cached(s: Session, p: Paragraph, cache: dict | None) -> tuple[set[int], dict[int, str]]:
    rnd = round_of(s, p)
    if cache is None:
        return _amount_sources(s, rnd)
    if rnd.id not in cache:  # 一次算出兩組，各自以不同的鍵存
        cache[rnd.id], cache[("unverified", rnd.id)] = _amount_sources(s, rnd)
    return cache[rnd.id], cache[("unverified", rnd.id)]


def allowed_amounts(s: Session, p: Paragraph, cache: dict | None = None) -> set[int]:
    """書狀裡可以直接寫的金額（設計 5.5）。cache 讓一次匯出檢查只查一次（以輪次 id 為鍵）。"""
    return _amounts_cached(s, p, cache)[0]


def unverified_amounts(s: Session, p: Paragraph, cache: dict | None = None) -> dict[int, str]:
    """只出現在還沒對照原圖的辨識文字裡的金額 → 第一個出處的頁標籤（計畫 2A）。"""
    return _amounts_cached(s, p, cache)[1]


def _amount_sources(s: Session, rnd: Round) -> tuple[set[int], dict[int, str]]:
    """起訴狀確認過的請求項目與加總、本案卷證、本輪對造書狀、本輪主張原文裡的金額。
    回傳（可以直接寫的金額, 只出現在未對照原圖辨識文字裡的金額 → 頁標籤）。"""
    allowed: set[int] = set()
    unverified: dict[int, str] = {}
    current: dict = {}

    def add(text: str, page_id, pt=None) -> None:
        if pt is None and page_id:
            key = str(page_id)
            if key not in current:
                current[key] = current_page_text(s, UUID(key))
            pt = current[key]
        if needs_check(pt):
            label = page_label(s, pt.page_id)
            for n in parse_amounts(text):
                unverified.setdefault(n, label)
        else:
            allowed.update(parse_amounts(text))

    if rnd.brief_kind == "complaint":
        items = confirmed_requests(s, rnd.id)
        allowed.update(c.amount for c in items if c.request_type == "monetary" and c.amount is not None)
        allowed.add(round_total(s, rnd.id))
    pages = s.scalars(select(Page).join(Document, Document.id == Page.document_id)
                      .where(Document.case_id == rnd.case_id, Document.kind == "evidence")
                      .order_by(Document.created_at, Page.page_no)).all()
    for pg in pages:
        pt = current_page_text(s, pg.id)
        if pt is not None:
            add(pt.text, pg.id, pt)
    listed = set()
    for op in opponent_pages(s, rnd.id):
        listed.add(op.page.id)
        if op.text is not None:
            add(op.text.text, op.page.id, op.text)
    from app.opponent_exhibits import opponent_exhibit_pages
    for pt in opponent_exhibit_pages(s, rnd.case_id):  # 前幾輪的對方證物也可能被引用（對方證物計畫）
        if pt.page_id not in listed:
            add(pt.text, pt.page_id, pt)
    # 原文摘錄只算「確實在原文找到」的（設計 5.1）：畫面標紅「未找到原文」的摘錄可能是 AI 編的，不能拿來放行金額
    for c in s.scalars(select(Claim).where(Claim.round_id == rnd.id).order_by(Claim.seq)):
        if c.quote_verified:
            add(c.quote or "", c.page_id)
        for e in c.evidence or []:
            if e.get("found"):
                add(e.get("quote") or "", e.get("page_id"))
    return allowed, {n: label for n, label in unverified.items() if n not in allowed}


def amount_reasons(s: Session, p: Paragraph, text: str, cache: dict | None = None) -> list[str]:
    """段落裡（不含引用標記展開的引文）每個對不上的金額，律師按「已核對金額」前都擋下。"""
    if p.kind == "statement" and round_of(s, p).amounts_checked_by is not None:
        return []  # 律師已勾「金額已自行核對」，涵蓋聲明裡自己寫的金額
    found = list(dict.fromkeys(parse_amounts(MARKER_RE.sub("", text))))
    if not found:
        return []
    allowed = allowed_amounts(s, p, cache) | set(p.amounts_confirmed or [])
    pending = unverified_amounts(s, p, cache)
    return [f"amount_image_unverified:{n}:{pending[n]}" if n in pending else f"amount_unverified:{n}"
            for n in found if n not in allowed]


def verify_paragraph(s: Session, p: Paragraph, cache: dict | None = None) -> list[str]:
    """回傳段落層原因並寫入 p.verify_reasons；同時重新驗證每筆引用。"""
    cites = {c.code: c for c in paragraph_citations(s, p.id)}
    markers = MARKER_RE.findall(p.text or "")
    marker_codes = {code for _, code in markers}
    reasons: list[str] = []
    for code in sorted(marker_codes - set(cites)):
        reasons.append(f"marker_without_citation:{code}")
    for code in sorted(set(cites) - marker_codes):
        reasons.append(f"citation_without_marker:{code}")
    for code in sorted({code for kind, code in markers if kind == "quote"}):
        if code in cites and not cites[code].quote.strip():
            reasons.append(f"quote_marker_without_quote:{code}")
    for code in sorted(cites):  # 同一代碼給了不同引文（見 attach_citations）
        if QUOTE_CONFLICT in (cites[code].verify_reasons or []):
            reasons.append(f"citation_quote_conflict:{code}")
    if MARKER_RE.search(p.heading or ""):  # 標題不展開標記，有標記就會原樣印進書狀
        reasons.append("marker_in_heading")
    # 金額標記 {{amount}} 只能在起訴狀的聲明段落；其他地方一律標錯（設計 5.2）
    body = (p.text or "").replace(AMOUNT_MARKER, "")
    if AMOUNT_MARKER in (p.text or "") and not amount_marker_allowed(s, p):
        reasons.append("amount_marker_misplaced")
    if has_malformed_marker(p.heading) or has_malformed_marker(body):
        reasons.append("malformed_marker")
    if any(INVALID_XML_CHARS.search(x or "") for x in [p.heading, p.text, *(c.quote for c in cites.values())]):
        reasons.append("control_char")
    for hit in scan_unbound_references(p.heading) + scan_unbound_references(body):
        reason = f"unbound_reference:{hit}"
        if reason not in reasons:
            reasons.append(reason)
    for hit in scan_numbered_cross_references(strip_heading_number(p.heading)) + scan_numbered_cross_references(p.text):
        reason = f"numbered_cross_reference:{hit}"
        if reason not in reasons:
            reasons.append(reason)
    ops = [op.text for op in opponent_pages(s, _round_id_for(s, p)) if op.text is not None]
    checked = [pt.text for pt in ops if not needs_check(pt)]
    pending = [pt.text for pt in ops if needs_check(pt)]
    for part in (p.heading, p.text):
        missing, unverified = scan_inline_quotes_split(part, checked, pending)
        reasons += [f"inline_quote_unverified:{head}" for head in missing]
        reasons += [f"inline_quote_image_unverified:{head}" for head in unverified]
    reasons.extend(amount_reasons(s, p, f"{p.heading or ''}\n{body}", cache))  # 標題也會印進書狀，金額一起核對
    if p.kind == "statement":
        reasons.extend(statement_reasons(s, p))
    for c in cites.values():
        verify_citation(s, c)
    p.verify_reasons = reasons
    s.flush()
    return reasons


@dataclass(frozen=True)
class Blocker:
    paragraph_seq: int
    reason: str
    paragraph_no: int = 0  # 畫面與匯出上的編號（只算未刪除的內文段落）；0＝聲明或整份草稿的問題
    scope: str = "paragraph"  # paragraph＝內文段落；statement＝聲明區塊；draft＝整份草稿


def paragraph_blockers(s: Session, p: Paragraph, cache: dict | None = None) -> list[str]:
    """重新驗證並回傳該段所有阻擋原因（含 unreviewed）。"""
    out: list[str] = []
    reasons = verify_paragraph(s, p, cache)
    if p.status != "reviewed":
        out.append("unreviewed")
    out.extend(reasons)
    for c in paragraph_citations(s, p.id):
        if c.verify_status not in ("ok", "warn"):
            out.append(f"citation_failed:{c.code}")
        elif c.kind == "evidence" and c.page_text_id and needs_check(s.get(PageText, c.page_text_id)):
            out.append(f"image_unverified:{c.code}")  # 引用的是辨識文字：律師要先對照原圖（設計 6.4）
        elif c.verify_status == "warn" and not c.court_confirmed:
            out.append(f"party_claim_unconfirmed:{c.code}")
        elif c.kind == "judgment" and not c.lawyer_confirmed:
            out.append(f"judgment_unconfirmed:{c.code}")
    from app.support import service as support  # 支持度（設計 7.3）；函式內 import 避免循環
    out.extend(support.support_blockers(s, p, cache))
    return out


def is_removed(p: Paragraph) -> bool:
    """標題與內文都清空的內文段落視為已刪除：不顯示、不審、不匯出（修改紀錄仍保留原文）。
    聲明段落清空也不算刪除，改由 statement_missing 擋下匯出。"""
    if p.kind == "statement":
        return False
    return not (p.heading or "").strip() and not (p.text or "").strip()


def body_paragraphs(s: Session, draft_id: UUID) -> list[Paragraph]:
    """未刪除的內文段落，依順序；編號只算這些（聲明不佔編號）。"""
    return [p for p in s.scalars(select(Paragraph).where(Paragraph.draft_id == draft_id, Paragraph.kind == "body")
                                 .order_by(Paragraph.seq)) if not is_removed(p)]


def export_blockers(s: Session, draft_id: UUID) -> list[Blocker]:
    """匯出前一律重新驗證，不信任先前存的結果。"""
    out: list[Blocker] = [Blocker(0, r, 0, "draft") for r in draft_reasons(s, draft_id)]
    from app.header_extract import header_unverified  # 函式內 import，避免循環
    case_id = s.get(Round, s.get(Draft, draft_id).round_id).case_id
    if header_unverified(s, case_id):  # 從對方書狀自動帶入的狀頭還沒核對（設計 6.6）
        out.append(Blocker(0, "header_unverified", 0, "draft"))
    cache: dict = {}  # 可用金額同一輪只查一次
    statement = statement_of(s, draft_id)
    if statement is not None and (statement.text or "").strip():
        out.extend(Blocker(0, r, 0, "statement") for r in paragraph_blockers(s, statement, cache))
    paragraphs = body_paragraphs(s, draft_id)
    if not paragraphs:
        return out + [Blocker(0, "empty_draft", 0, "draft")]
    for n, p in enumerate(paragraphs, start=1):
        out.extend(Blocker(p.seq, r, n) for r in paragraph_blockers(s, p, cache))
    return out
