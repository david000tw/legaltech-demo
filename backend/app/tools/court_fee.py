"""財產權訴訟裁判費（民事訴訟法第77條之13，含臺灣高等法院提高徵收額數標準〔第77條之27〕）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.tools import DISCLAIMER, Step

BASIS = "民事訴訟法第77條之13、第77條之27"
APPEAL_BASIS = BASIS + "、第77條之16"
ROUNDING_NOTE = "尾數處理以法院通知為準"
FLOOR_LIMIT, FLOOR_FEE = 100_000, 1_500
# (級距上限, 每萬元費額)；None＝沒有上限
TIERS = ((1_000_000, 130), (10_000_000, 117), (100_000_000, 88), (1_000_000_000, 77), (None, 66))
UNIT = 10_000


@dataclass
class FeeResult:
    fee: int | None
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _wan(n: int) -> str:
    return f"{n // UNIT:,}萬元" if n % UNIT == 0 else f"{n:,}元"


def first_instance(amount: int) -> FeeResult:
    steps = [Step(f"{_wan(FLOOR_LIMIT)}以下部分", f"{FLOOR_FEE:,}元", BASIS)]
    fee, lower = FLOOR_FEE, FLOOR_LIMIT
    for upper, rate in TIERS:
        if amount <= lower:
            break
        portion = (amount if upper is None else min(amount, upper)) - lower
        units = -(-portion // UNIT)  # 不滿萬元以萬元計
        part = units * rate
        fee += part
        span = f"逾{_wan(lower)}" + (f"至{_wan(upper)}" if upper else "")
        steps.append(Step(f"{span}部分", f"{portion:,}元，以{units:,}萬元計，每萬元{rate}元＝{part:,}元",
                          BASIS + "（不滿萬元以萬元計）"))
        if upper is None:
            break
        lower = upper
    steps.append(Step("第一審裁判費", f"{fee:,}元", BASIS))
    return FeeResult(fee, steps, [DISCLAIMER])


def appeal(amount: int) -> FeeResult:
    """第二審、第三審：依第一審費額加徵十分之五，四捨五入到元。"""
    base = first_instance(amount)
    fee = int((Decimal(base.fee) * Decimal("1.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    steps = [*base.steps, Step("上訴審裁判費（加徵十分之五）", f"{base.fee:,}元 × 1.5 ＝ {fee:,}元", APPEAL_BASIS)]
    return FeeResult(fee, steps, [DISCLAIMER, ROUNDING_NOTE])


def non_property() -> FeeResult:
    return FeeResult(None, [Step("非財產權訴訟", "依民事訴訟法第77條之14", "民事訴訟法第77條之14")], [DISCLAIMER])
