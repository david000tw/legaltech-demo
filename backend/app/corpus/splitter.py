"""判決切段：依段落標題把全文分成主文、當事人主張、法院判斷與其他。

規則（計畫 1 Task 7）：
1. 刪除只含兩位數字的行（行號欄）與獨立成行的「第X頁」頁尾（阿拉伯或中文數字）。
2. 「主文」到「事實及理由」或「理由」之前為 main_text。
3. 「事實及理由」之後，依頂層標題（一、二、…／甲、乙、…）分類：
   - 法院判斷（得心證之理由、本院之判斷、法院之判斷…）與其後的「綜上所述」為 court_reasoning；
   - 原告、被告、上訴人、被上訴人的主張、抗辯、略謂等為 party_claim；
   - 「事證已臻明確」段也是法院自己的論述，列為 court_reasoning；
   - 訴訟費用、假執行、據上論結、不爭執事項、原審判決經過等為 other。
4. court_reasoning 與 party_claim 再依 ㈠㈡…、(一)(二)…、1. 2. … 切成多段。
5. 標題只在「段落開頭」才算：上一行須以句號、冒號、分號或右括號結尾，或本身是短標題行；
   否則「…百分之7／0、百分之30」這種換行後剛好以「數字、」開頭的句子會被切斷。
   上一行以「頁」加空白結尾（「本院卷第39-61頁　　」）也算段落開頭。

計畫 1B（設計 5.4）：
6. 認得第一層標題「壹、貳…」「甲、乙…」。第一層是當事人主張或法院判斷時，底下的「一、二、」跟著第一層走
   （「壹、原告主張」底下的「一、按…」是當事人自己的法律主張，不是法院見解）；第一層是程序、實體等
   中性標題時，底下各自分類。
7. 法院判斷區：遇到法院判斷標題，或標題開頭是「本件爭點」「經查」才進入；以「按」開頭的段落只有在
   判斷區內才算法院判斷（判斷區外的「一、按訴狀送達後…」是程序說明）。判斷區內的頂層段落，除了明確的
   程序尾段（訴訟費用、假執行…）以外，一律算 court_reasoning，包括以「原告主張…」開頭的整理段。
8. 重置：第一層標題，或標題含「本訴」「反訴」「追加」「程序」「實體」，離開法院判斷區、重新分類。
9. 上架檢查 split_problems：每則判決要有當事人主張、法院判斷，且法院判斷字數不低於全文的 COURT_MIN_RATIO。
"""
from __future__ import annotations

import re

_LINE_NO = re.compile(r"\s*\d{2}\s*")
_PAGE_FOOTER = re.compile(r"\s*第\s*[〇一二三四五六七八九十百\d]+\s*頁\s*")
_MAIN = re.compile(r"^\s*主\s*文\s*$")
_FACTS = re.compile(r"^\s*(事\s*實\s*及\s*)?理\s*由\s*$")
_TOP = re.compile(r"^\s*[一二三四五六七八九十]+、")
_LEVEL1 = re.compile(r"^\s*(?:[壹貳參肆伍陸柒捌玖拾]+|[甲乙丙丁戊己庚辛])、")  # 第一層：壹貳、甲乙
_HEAD_NO = re.compile(r"^\s*(?:[壹貳參肆伍陸柒捌玖拾]+|[一二三四五六七八九十]+|[甲乙丙丁戊己庚辛])、\s*")
_RESET = re.compile(r"本訴|反訴|追加|程序|實體")
_PAGE_END = re.compile(r"頁[ \t　\xa0]+$")
_SUB = re.compile(r"^\s*([㈠-㈩]|[⑴-⒇]|[（(][一二三四五六七八九十]+[)）]|\d{1,2}[.．、](?!\d))")
_REASONING = re.compile(r"得心證|本院之判斷|法院之判斷|本院判斷|本院得心證|綜上所述|事證已臻明確")
_REASONING_START = re.compile(r"(?:本件爭點|經查(?!封))")  # 只看標題開頭：「經查封拍賣」不算
_OTHER = re.compile(r"訴訟費用|假執行|據上論結|爭執事項|原審|程序方面|實體方面|職權宣告|勝訴部分|簡易程序")
_CLOSERS = tuple("。：:；;」）)")
_TRIM = " \t\u3000\xa0ˉ"  # 空白、tab、全形空白、不換行空白、PDF 常見的 ˉ
SHORT_HEADING = 12
_PARTY = re.compile(r"(原告|被告|上訴人|被上訴人|聲請人|相對人).*?(主張|起訴|則以|抗辯|辯稱|答辯|略謂|部分|稱)")
_DATE = re.compile(r"^\s*中\s*華\s*民\s*國")
COURT_MIN_RATIO = 0.15


def _clean_lines(full_text: str) -> list[str]:
    return [ln for ln in full_text.split("\n")
            if not _LINE_NO.fullmatch(ln) and not _PAGE_FOOTER.fullmatch(ln)]


def _starts_block(prev: str | None) -> bool:
    """上一行結束了一句話或是短標題，本行才可能是新段落的開頭。"""
    if prev is None or _PAGE_END.search(prev):
        return True
    p = prev.strip(_TRIM)
    return not p or p.endswith(_CLOSERS) or (len(p) <= SHORT_HEADING and "。" not in p)


def _classify_top(heading: str, in_court: bool = False) -> str:
    body = _HEAD_NO.sub("", heading, count=1)
    if _REASONING.search(heading) or _REASONING_START.match(body):
        return "court_reasoning"
    if _OTHER.search(heading):
        return "other"
    if in_court:  # 判斷區內：「按…」見解段、以「原告主張」開頭的整理段，都是法院判斷
        return "court_reasoning"
    if _PARTY.search(heading):
        return "party_claim"
    return "other"


def split_judgment(full_text: str) -> list[tuple[str, str]]:
    """回傳 [(passage_type, text)]，text 保留原文換行。"""
    chunks: list[list] = []  # [kind, lines]

    def start(kind: str, line: str | None = None) -> None:
        chunks.append([kind, [] if line is None else [line]])

    state = "other"
    in_court = False
    level1: str | None = None  # 目前第一層（壹貳、甲乙）的類型；中性標題時為 None
    start(state)
    prev: str | None = None
    for line in _clean_lines(full_text):
        at_block = _starts_block(prev)
        prev = line
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
            if at_block and (_LEVEL1.match(line) or _TOP.match(line)):
                if _LEVEL1.match(line):
                    in_court = False
                    kind = _classify_top(line)
                    level1 = kind if kind in ("party_claim", "court_reasoning") else None
                else:
                    if _RESET.search(line):
                        in_court = False
                    if level1 is not None and not _OTHER.search(line):
                        kind = level1  # 子層跟著第一層：主張底下的「按…」仍是主張
                    else:
                        kind = _classify_top(line, in_court)
                in_court = in_court or kind == "court_reasoning"
                state = kind if kind != "other" else "facts_other"
                start(kind, line)
                continue
            if state in ("court_reasoning", "party_claim") and at_block and _SUB.match(line):
                start(state, line)
                continue
        chunks[-1][1].append(line)

    out = []
    for kind, lines in chunks:
        text = "\n".join(lines).strip()
        if text:
            out.append((kind, text))
    return out


def split_problems(passages: list[tuple[str, str]], full_text: str, whitelisted: bool) -> list[str]:
    """上架檢查：沒有當事人主張、沒有法院判斷、或法院判斷太短，就回傳問題代碼；白名單判決不檢查。"""
    if whitelisted:
        return []
    problems = []
    if not any(k == "party_claim" for k, _ in passages):
        problems.append("no_party_claim")
    court = sum(len(t) for k, t in passages if k == "court_reasoning")
    if court == 0:
        problems.append("no_court_reasoning")
    elif court < COURT_MIN_RATIO * len(full_text):
        problems.append("court_reasoning_too_short")
    return problems
