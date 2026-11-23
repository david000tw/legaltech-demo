"""判決切段：依段落標題把全文分成主文、當事人主張、法院判斷與其他。

規則（計畫 1 Task 7）：
1. 刪除只含兩位數字的行（行號欄）與「第X頁」頁尾。
2. 「主文」到「事實及理由」或「理由」之前為 main_text。
3. 「事實及理由」之後，依頂層標題（一、二、…／甲、乙、…）分類：
   - 法院判斷（得心證之理由、本院之判斷、法院之判斷…）與其後的「綜上所述」為 court_reasoning；
   - 原告、被告、上訴人、被上訴人的主張、抗辯、略謂等為 party_claim；
   - 訴訟費用、假執行、事證已臻明確、據上論結、不爭執事項、原審判決經過等為 other。
4. court_reasoning 與 party_claim 再依 ㈠㈡…、(一)(二)…、1. 2. … 切成多段。
"""
from __future__ import annotations

import re

_LINE_NO = re.compile(r"\s*\d{2}\s*")
_PAGE_FOOTER = re.compile(r"\s*第\s*\d+\s*頁.*")
_MAIN = re.compile(r"^\s*主\s*文\s*$")
_FACTS = re.compile(r"^\s*(事\s*實\s*及\s*)?理\s*由\s*$")
_TOP = re.compile(r"^\s*([一二三四五六七八九十]+|[甲乙丙丁戊])、")
_SUB = re.compile(r"^\s*([㈠-㈩]|[⑴-⒇]|[（(][一二三四五六七八九十]+[)）]|\d{1,2}[.．、](?!\d))")
_REASONING = re.compile(r"得心證|本院之判斷|法院之判斷|本院判斷|本院得心證|綜上所述")
_OTHER = re.compile(r"訴訟費用|假執行|事證已臻明確|據上論結|爭執事項|原審|程序方面|實體方面|職權宣告")
_PARTY = re.compile(r"(原告|被告|上訴人|被上訴人|聲請人|相對人).*?(主張|起訴|則以|抗辯|辯稱|答辯|略謂|部分)")
_DATE = re.compile(r"^\s*中\s*華\s*民\s*國")


def _clean_lines(full_text: str) -> list[str]:
    return [ln for ln in full_text.split("\n")
            if not _LINE_NO.fullmatch(ln) and not _PAGE_FOOTER.fullmatch(ln)]


def _classify_top(heading: str) -> str:
    if _REASONING.search(heading):
        return "court_reasoning"
    if _OTHER.search(heading):
        return "other"
    if _PARTY.search(heading):
        return "party_claim"
    return "other"


def split_judgment(full_text: str) -> list[tuple[str, str]]:
    """回傳 [(passage_type, text)]，text 保留原文換行。"""
    chunks: list[list] = []  # [kind, lines]

    def start(kind: str, line: str | None = None) -> None:
        chunks.append([kind, [] if line is None else [line]])

    state = "other"
    start(state)
    for line in _clean_lines(full_text):
        if _MAIN.match(line):
            state = "main_text"
            start(state, line)
            continue
        if _FACTS.match(line):
            state = "facts"
            start("other", line)
            continue
        if state == "facts" or state in ("court_reasoning", "party_claim", "facts_other"):
            if _DATE.match(line):
                state = "tail"
                start("other", line)
                continue
            if _TOP.match(line):
                kind = _classify_top(line)
                state = kind if kind != "other" else "facts_other"
                start(kind, line)
                continue
            if state in ("court_reasoning", "party_claim") and _SUB.match(line):
                start(state, line)
                continue
        chunks[-1][1].append(line)

    out = []
    for kind, lines in chunks:
        text = "\n".join(lines).strip()
        if text:
            out.append((kind, text))
    return out
