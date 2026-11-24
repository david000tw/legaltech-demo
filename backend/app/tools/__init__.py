"""裁判費、期限、繕本份數試算（設計 5.10）：純函式、不用 AI，每個結果附逐步依據。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

DISCLAIMER = "試算僅供參考，律師須自行核對"
WEEKDAYS = "一二三四五六日"


@dataclass
class Step:
    label: str
    value: str
    basis: str


def step_json(s: Step) -> dict:
    return asdict(s)


def roc_date(d: date) -> str:
    """民國日期，例：民國115年3月31日（星期二）。"""
    return f"民國{d.year - 1911}年{d.month}月{d.day}日（星期{WEEKDAYS[d.weekday()]}）"
