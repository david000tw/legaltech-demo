"""案型登錄表（設計 5.1）：案型之間的差異集中在這裡，不分散在各處。

爭點清單只是提示 AI「這類案件常見的爭點」，不限制拆主張；內容試點前由合作律師審定。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.text_norm import normalize_text

Procedure = Literal["civil", "family"]


@dataclass(frozen=True)
class CaseType:
    code: str
    label: str
    procedure: Procedure
    reference_date_label: str
    issues: tuple[str, ...]


CASE_TYPES: dict[str, CaseType] = {ct.code: ct for ct in (
    CaseType("traffic_tort", "車禍侵權", "civil", "事故發生日",
             ("過失與路權", "與有過失", "各項損害（醫療、工作損失、車損折舊、慰撫金）")),
    CaseType("spousal_rights", "侵害配偶權", "civil", "侵害行為終了日",
             ("是否侵害配偶身分法益", "故意過失（是否知悉對方已婚）", "共同侵權", "慰撫金酌定因素", "時效")),
    CaseType("estate_partition", "分割遺產（含特留分）", "family", "繼承開始日",
             ("遺產範圍", "應繼分", "特留分是否受侵害與扣減", "生前特種贈與歸扣", "分割方法", "債務")),
    CaseType("nominee_registration", "借名登記返還", "civil", "借名關係終止日",
             ("借名關係是否存在與舉證", "終止事由", "返還請求權基礎", "出名人死亡與繼承", "時效起算")),
)}
DEFAULT_CASE_TYPE = "traffic_tort"
SIDE_LABEL = {"plaintiff": "原告", "defendant": "被告"}

# 當事人（設計 5.2）：角色從固定清單選；歸屬分我方、對造、立場與我方相同三種
PARTY_ROLES = ("原告", "被告", "上訴人", "被上訴人", "反訴原告", "反訴被告", "聲請人", "相對人")
ALIGNMENTS = ("ours", "opposing", "aligned")
ALIGNMENT_LABEL = {"ours": "我方", "opposing": "對造", "aligned": "立場與我方相同"}
# 立場相同者的規則（設計 5.2）：案件背景與起草提示詞用同一句，沒有例外
ALIGNED_RULE = "只反駁歸屬為「對造」的當事人；「立場與我方相同」的當事人提出的論點一律不得反駁，必要時說明我方同意之處；其立場說明中與我方意見不同的部分，不反駁也不表示同意，留給律師處理。"

# 程序決定書狀名稱與預設受文：(書狀名稱, 受文的庭別)
PROCEDURES: dict[str, tuple[str, str]] = {"civil": ("民事準備書狀", "民事庭"), "family": ("家事準備書狀", "家事法庭")}
PROCEDURE_LABEL = {"civil": "民事", "family": "家事"}

# 「年度」的「度」用佔有量詞：「113年度　字第　號」不能把「度」當成字別
_CASE_WORD = re.compile(r"年度?+(\D+?)字第")
# 案號不是完整格式時（「113家訴5」「113年度家訴字5號」）：去掉數字與年度、字、第、號，剩下的就是字別
_NOT_WORD = re.compile(r"[\d〇零一二三四五六七八九十百千\s年度字第號()（）]")
_DIGIT = re.compile(r"[\d〇零一二三四五六七八九十百千]")
# 字別要像字別：1～8 個漢字，且含常見字別用字。「尚未分案」「待補」「○○」「XX」這類暫代文字不算（最終審查 I1）。
_WORD_SHAPE = re.compile(r"^[\u4e00-\u9fff]{1,8}$")
_WORD_CHARS = set("訴簡上抗調繼聲執全家婚親再救小促重移監輔護養財非易司債票拍消勞智國交選原金醫建審")


def _looks_like_word(word: str) -> bool:
    return bool(_WORD_SHAPE.match(word)) and bool(_WORD_CHARS & set(word))


def case_type_of(code: str | None) -> CaseType:
    return CASE_TYPES.get(code or "", CASE_TYPES[DEFAULT_CASE_TYPE])


def _word(case_number: str | None) -> str | None:
    """案號的字別，例如「113年度家繼訴字第89號」→「家繼訴」。"""
    text = normalize_text(case_number or "")
    m = _CASE_WORD.search(text)
    if m:
        return m.group(1) if _looks_like_word(m.group(1)) else None
    if not _DIGIT.search(text):
        return None
    rest = _NOT_WORD.sub("", text)
    return rest if _looks_like_word(rest) else None


def procedure_for(case_type: str | None, case_number: str | None) -> str:
    """案號有填（且看得出字別）時以字別判定：含「家」就是家事；否則用案型的預設程序。"""
    word = _word(case_number)
    if word is not None:
        return "family" if "家" in word else "civil"
    return case_type_of(case_type).procedure


def procedure_warning(case_type: str | None, case_number: str | None) -> str | None:
    word = _word(case_number)
    ct = case_type_of(case_type)
    if word is None:
        if not (case_number or "").strip():
            return None
        return (f"無法判讀案號「{case_number.strip()}」的字別，書狀名稱與受文先依「{ct.label}」預設的"
                f"{PROCEDURE_LABEL[ct.procedure]}程序（{PROCEDURES[ct.procedure][0]}）；"
                "請改成「113年度家訴字第5號」這種格式，或確認程序。")
    actual = procedure_for(case_type, case_number)
    if actual == ct.procedure:
        return None
    return (f"案號字別「{word}」屬{PROCEDURE_LABEL[actual]}程序，與「{ct.label}」預設的{PROCEDURE_LABEL[ct.procedure]}"
            f"程序不同；書狀名稱與受文依案號（{PROCEDURES[actual][0]}），請確認案號與案型。")


BRIEF_KINDS = {"complaint": "起訴狀", "answer": "答辯狀", "preparatory": "準備書狀"}
MONEY_CASE_TYPES = {"traffic_tort", "spousal_rights"}
_NUMERALS = "一二三四五六七八九十"


def brief_title(procedure: str, brief_kind: str = "preparatory") -> str:
    """書狀名稱＝程序（民事／家事）＋種類（起訴狀／答辯狀／準備書狀），設計 5.1。"""
    return PROCEDURE_LABEL[procedure] + BRIEF_KINDS[brief_kind]


def allowed_brief_kinds(our_side: str) -> list[str]:
    return ["complaint", "preparatory"] if our_side == "plaintiff" else ["answer", "preparatory"]


def default_brief_kind(our_side: str, has_rounds: bool) -> str:
    if has_rounds:
        return "preparatory"
    return "complaint" if our_side == "plaintiff" else "answer"


def auto_money_statement(parties, joint: bool) -> bool:
    """金錢那一項能不能由系統自動產生：多位原告，或多位被告但不連帶時，金額怎麼分配要律師自己寫（設計 5.2）。"""
    plaintiffs = sum(1 for p in parties if p.role == "原告" and p.alignment == "ours")
    defendants = sum(1 for p in parties if p.role == "被告")
    return not (plaintiffs > 1 or (defendants > 1 and not joint))


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{_NUMERALS[i]}、{line}" for i, line in enumerate(lines))


def complaint_statement(case_type: str, *, has_money: bool, has_non_money: bool, joint: bool, provisional: bool,
                        auto_money: bool, variant: str | None) -> str:
    """訴之聲明範本（設計 5.2，待律師審定）：金額一律是系統欄位 {{amount}}，AI 不寫金額。"""
    lines: list[str] = []
    if has_money:
        if auto_money:
            lines.append(f"被告應{'連帶' if joint else ''}給付原告新臺幣{{{{amount}}}}元，"
                         "及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。")
        else:  # 多位原告或非連帶被告：留空格讓律師自己寫
            lines.append("被告應給付原告新臺幣○○元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。")
    non_money = has_non_money or case_type in ("estate_partition", "nominee_registration")
    if non_money:
        if case_type == "estate_partition":
            lines.append("兩造公同共有被繼承人○○○所遺如附表所示之遺產，准依附表所示方法分割。")
        elif variant == "deceased":
            lines.append("被告應就被繼承人○○○所遺坐落○○之不動產辦理繼承登記後，將所有權移轉登記予原告。")
        else:
            lines.append("被告應將坐落○○之不動產（權利範圍○○）所有權移轉登記予原告。")
    lines.append("訴訟費用由兩造按附表所示應繼分比例負擔。" if case_type == "estate_partition" else "訴訟費用由被告負擔。")
    if has_money and provisional:  # 分割遺產（形成判決）與移轉登記（命為意思表示）不能聲請假執行
        lines.append("就第一項聲明，原告願供擔保，請准宣告假執行。" if non_money else "原告願供擔保，請准宣告假執行。")
    return _numbered(lines)


def answer_statement(case_type: str) -> str:
    """答辯聲明範本（設計 5.3，待律師審定）：對造沒有聲請假執行的案型，不寫假執行那兩句。"""
    if case_type in MONEY_CASE_TYPES:
        return _numbered(["原告之訴及假執行之聲請均駁回。", "訴訟費用由原告負擔。", "如受不利判決，被告願供擔保請准宣告免為假執行。"])
    return _numbered(["原告之訴駁回。", "訴訟費用由原告負擔。"])


def default_addressee(court: str | None, procedure: str) -> str:
    return f"{(court or '').strip()}{PROCEDURES[procedure][1]}"
