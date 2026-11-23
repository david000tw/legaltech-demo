"""給 AI 的案件背景（設計 5.1）：案型、程序、書狀名稱、我方立場與本類常見爭點。

放在每個 AI 步驟的使用者訊息開頭；系統提示詞維持通用，不寫任何案型專用字詞。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.case_types import (
    ALIGNED_RULE, ALIGNMENT_LABEL, PROCEDURE_LABEL, SIDE_LABEL, brief_title, case_type_of, procedure_for,
)
from app.models import Case, CaseParty


def case_context(s: Session, case: Case) -> str:
    ct = case_type_of(case.case_type)
    procedure = procedure_for(case.case_type, case.case_number)
    lines = [
        f"案型：{ct.label}",
        f"程序：{PROCEDURE_LABEL[procedure]}事件；書狀名稱：{brief_title(procedure)}",
        f"我方立場：{SIDE_LABEL[case.our_side]}",
        *([f"{ct.reference_date_label}：{case.reference_date.isoformat()}"] if case.reference_date else []),
        "本類案件常見爭點（僅供參考，不限制拆主張或論述）：",
        *(f"・{issue}" for issue in ct.issues),
    ]
    parties = list(s.scalars(select(CaseParty).where(CaseParty.case_id == case.id).order_by(CaseParty.seq)))
    if parties:
        lines.append("當事人：")
        for p in parties:
            rep = f"，法定代理人{p.legal_rep}" if p.legal_rep else ""
            note = f"；立場說明：{p.position_note}" if p.position_note else ""
            lines.append(f"・{p.role} {p.name}（{ALIGNMENT_LABEL[p.alignment]}{rep}）{note}")
        if any(p.alignment == "aligned" for p in parties):
            lines.append(f"規則：{ALIGNED_RULE}")
    return "【案件背景】\n" + "\n".join(lines)
