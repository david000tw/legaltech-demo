"""上訴、抗告期限試算：送達生效 → 翌日起算 → 加在途期間 → 屆滿日遇假日順延。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.tools import DISCLAIMER, Step, roc_date

TYPHOON = "颱風等臨時停止上班不在行事曆內，請自行確認"
UNCOVERED = "行事曆資料未涵蓋，只順延週末"
FAMILY_ERROR = "家事訴訟事件請選上訴；家事非訟事件請選抗告；或改用自訂天數"

# 送達方式 → (經過天數, 說明, 依據)
METHODS = {
    "direct": (0, "直接送達", "送達當日生效"),
    "deposit": (10, "寄存送達", "民事訴訟法第138條第2項：寄存之日起經10日發生效力"),
    "public": (20, "公示送達", "民事訴訟法第152條：經20日發生效力"),
    "foreign_public": (60, "對外國之公示送達", "民事訴訟法第152條：應於外國送達者經60日發生效力"),
    "continued_public": (1, "續行公示送達", "民事訴訟法第152條但書：黏貼牌示處之翌日發生效力"),
}
# (期限種類, 程序) → (天數, 依據)
PERIODS = {
    ("appeal", "civil"): (20, "民事訴訟法第440條"),
    ("interlocutory", "civil"): (10, "民事訴訟法第487條"),
    ("appeal", "family_litigation"): (20, "家事事件法第51條準用民事訴訟法第440條"),
    ("interlocutory", "family_nonlitigation"): (10, "家事事件法第92條、第93條"),
}
WHO = {"party": "本人", "attorney": "訴訟代理人"}


@dataclass
class Service:
    who: Literal["party", "attorney"]
    served_on: date
    method: str


@dataclass
class DeadlineResult:
    due: date
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def effective_date(served_on: date, method: str) -> tuple[date, Step]:
    days, name, basis = METHODS[method]
    eff = served_on + timedelta(days=days)
    return eff, Step(f"{name}生效日", roc_date(eff), basis)


def _period(kind: str, procedure: str, custom_days: int | None) -> tuple[int, str]:
    if kind == "custom":
        if not custom_days or custom_days < 1:
            raise ValueError("自訂天數至少 1 日")
        return custom_days, "律師自訂天數"
    if (kind, procedure) not in PERIODS:
        raise ValueError(FAMILY_ERROR)
    return PERIODS[(kind, procedure)]


def compute(kind: str, procedure: str, services: list[Service], travel_days: int, attorney_local: bool,
            custom_days: int | None, calendar: Callable[[date], bool | None]) -> DeadlineResult:
    if not services:
        raise ValueError("請至少填一筆送達")
    days, period_basis = _period(kind, procedure, custom_days)
    steps: list[Step] = []
    dated = []
    for sv in services:
        eff, st = effective_date(sv.served_on, sv.method)
        if len(services) > 1:
            st.label = f"{WHO[sv.who]}{st.label}"
        steps.append(st)
        dated.append((eff, sv))
    eff, chosen = min(dated, key=lambda x: x[0])
    if len(services) > 1:
        steps.append(Step("採用的送達", f"{WHO[chosen.who]}，{roc_date(eff)}生效", "本人與訴訟代理人中，生效日較早者"))
    steps.append(Step("期間起算日", roc_date(eff + timedelta(days=1)), "民法第120條第2項：始日不算入，自翌日起算"))
    steps.append(Step("期間", f"{days}日", period_basis))
    travel = 0 if attorney_local else max(travel_days, 0)
    if attorney_local:
        steps.append(Step("在途期間", "0日", "民事訴訟法第162條但書：訴訟代理人住居法院所在地，不扣除在途期間"))
    else:
        steps.append(Step("在途期間", f"{travel}日", "民事訴訟法第162條；法院訴訟當事人在途期間標準"))
    due = eff + timedelta(days=days + travel)
    steps.append(Step("屆滿日", roc_date(due), "生效日＋期間＋在途期間"))

    uncovered = False

    def holiday(d: date) -> bool:
        nonlocal uncovered
        v = calendar(d)
        if v is None:
            uncovered = True
            return d.weekday() >= 5
        return v

    moved = due
    while holiday(moved):
        moved += timedelta(days=1)
    if moved != due:
        steps.append(Step("順延後屆滿日", roc_date(moved), "民法第122條：期間末日為休息日，以其休息日之次日代之"))
    notes = [DISCLAIMER, TYPHOON] + ([UNCOVERED] if uncovered else [])
    return DeadlineResult(moved, steps, notes)
