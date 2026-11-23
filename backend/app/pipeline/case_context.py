"""給 AI 的案件背景（設計 5.1）：案型、程序、書狀名稱、我方立場與本類常見爭點。

放在每個 AI 步驟的使用者訊息開頭；系統提示詞維持通用，不寫任何案型專用字詞。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.case_types import PROCEDURE_LABEL, SIDE_LABEL, brief_title, case_type_of, procedure_for
from app.models import Case


def case_context(s: Session, case: Case) -> str:
    ct = case_type_of(case.case_type)
    procedure = procedure_for(case.case_type, getattr(case, "case_number", None))
    lines = [
        f"案型：{ct.label}",
        f"程序：{PROCEDURE_LABEL[procedure]}事件；書狀名稱：{brief_title(procedure)}",
        f"我方立場：{SIDE_LABEL[case.our_side]}",
        "本類案件常見爭點（僅供參考，不限制拆主張或論述）：",
        *(f"・{issue}" for issue in ct.issues),
    ]
    return "【案件背景】\n" + "\n".join(lines)
