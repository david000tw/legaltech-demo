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

# 程序決定書狀名稱與預設受文：(書狀名稱, 受文的庭別)
PROCEDURES: dict[str, tuple[str, str]] = {"civil": ("民事準備書狀", "民事庭"), "family": ("家事準備書狀", "家事法庭")}
PROCEDURE_LABEL = {"civil": "民事", "family": "家事"}

_CASE_WORD = re.compile(r"年度?(\D+?)字第")


def case_type_of(code: str | None) -> CaseType:
    return CASE_TYPES.get(code or "", CASE_TYPES[DEFAULT_CASE_TYPE])


def _word(case_number: str | None) -> str | None:
    """案號的字別，例如「113年度家繼訴字第89號」→「家繼訴」。"""
    m = _CASE_WORD.search(normalize_text(case_number or ""))
    return m.group(1) if m else None


def procedure_for(case_type: str | None, case_number: str | None) -> str:
    """案號有填（且看得出字別）時以字別判定：含「家」就是家事；否則用案型的預設程序。"""
    word = _word(case_number)
    if word is not None:
        return "family" if "家" in word else "civil"
    return case_type_of(case_type).procedure


def procedure_warning(case_type: str | None, case_number: str | None) -> str | None:
    word = _word(case_number)
    if word is None:
        return None
    ct = case_type_of(case_type)
    actual = procedure_for(case_type, case_number)
    if actual == ct.procedure:
        return None
    return (f"案號字別「{word}」屬{PROCEDURE_LABEL[actual]}程序，與「{ct.label}」預設的{PROCEDURE_LABEL[ct.procedure]}"
            f"程序不同；書狀名稱與受文依案號（{PROCEDURES[actual][0]}），請確認案號與案型。")


def brief_title(procedure: str) -> str:
    return PROCEDURES[procedure][0]


def default_addressee(court: str | None, procedure: str) -> str:
    return f"{(court or '').strip()}{PROCEDURES[procedure][1]}"
