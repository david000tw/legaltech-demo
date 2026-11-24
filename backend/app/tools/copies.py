"""繕本份數：法院 1 份＋他造各 1 份＋留存 1 份（民事訴訟法第119條）。"""
from __future__ import annotations

from app.models import CaseParty
from app.tools import Step

BASIS = "民事訴訟法第119條：書狀應按應受送達之他造人數提出繕本"


def count(parties: list[CaseParty]) -> tuple[int | None, list[Step]]:
    ours = next((p for p in sorted(parties, key=lambda p: p.seq) if p.alignment == "ours"), None)
    if ours is None:
        return None, [Step("繕本份數", "無法計算", "請先在當事人標示我方")]
    others = [p for p in parties if p.role != ours.role]  # 立場相同但列為他造角色的人，也要送繕本
    n = 1 + len(others) + 1
    names = "、".join(p.name for p in others) or "無"
    return n, [Step("法院", "1份", BASIS),
               Step("他造", f"{len(others)}份（{names}）", BASIS),
               Step("留存", "1份", "事務所留存"),
               Step("合計", f"{n}份", BASIS)]
