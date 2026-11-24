"""用量與費用統計（設計 5.12）：依來源、模型、步驟分組，以事務所的單價與匯率換算。"""
from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.registry import REGISTRY
from app.models import AIPrice, Case, CostSetting, UsageRecord

TAIPEI = ZoneInfo("Asia/Taipei")
MILLION = Decimal(1_000_000)
DEFAULT_RATE = Decimal(32)
DEFAULT_PRICE = {"input_usd": Decimal(4), "output_usd": Decimal(20), "cache_read_usd": Decimal("0.2"),
                 "cache_write_5m_usd": Decimal(5), "cache_write_1h_usd": Decimal(8)}
DEFAULT_ROWS = (("claude_code_local", "opus", "unmetered", ""),
                ("bedrock", "opus", "metered", "區域端點約加價 10%，請依實際帳單修改"))
UNMETERED_NOTE = "不按量計費，換算僅供參考"
UNSPLIT_NOTE = "這些紀錄沒有分開回報 5 分鐘與 1 小時快取寫入，以 1 小時單價計算"
UNPRICED_NOTE = "未設定單價的來源不計入合計"
DEFAULT_RATE_NOTE = "預設值，請依實際匯率修改"


def ensure_defaults(s: Session, firm_id: UUID) -> None:
    """事務所第一次打開用量頁時寫入預設單價與匯率；兩人同時打開也不會重複。"""
    if s.scalar(select(func.count()).select_from(AIPrice)) == 0:
        s.execute(insert(AIPrice).values([
            {"firm_id": firm_id, "source": src, "model": model, "billing": billing, "note": note, **DEFAULT_PRICE}
            for src, model, billing, note in DEFAULT_ROWS]).on_conflict_do_nothing())
    s.execute(insert(CostSetting).values(
        firm_id=firm_id, usd_twd=DEFAULT_RATE, rate_set_on=datetime.now(TAIPEI).date(), is_default=True,
    ).on_conflict_do_nothing())
    s.flush()


def _q4(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _month():
    return func.to_char(func.timezone("Asia/Taipei", UsageRecord.created_at), "YYYY-MM")


def usage_summary(s: Session, firm_id: UUID, *, month: str | None = None, case_id: UUID | None = None,
                  step: str | None = None, source: str | None = None, model: str | None = None) -> dict:
    conds = []
    if month:
        conds.append(_month() == month)
    if case_id:
        conds.append(UsageRecord.case_id == case_id)
    if step:
        conds.append(UsageRecord.step == step)
    if source:
        conds.append(UsageRecord.provider == source)
    if model:
        conds.append(UsageRecord.model == model)
    unsplit = and_(UsageRecord.cache_write_5m_tokens.is_(None), UsageRecord.cache_write_1h_tokens.is_(None))
    rows = s.execute(
        select(UsageRecord.provider, UsageRecord.model, UsageRecord.step, func.count(),
               func.count().filter(UsageRecord.result == "failed"),
               func.sum(UsageRecord.input_tokens), func.sum(UsageRecord.output_tokens),
               func.sum(UsageRecord.cache_read_tokens), func.sum(UsageRecord.cache_write_tokens),
               func.coalesce(func.sum(UsageRecord.cache_write_5m_tokens), 0),
               func.coalesce(func.sum(UsageRecord.cache_write_1h_tokens), 0),
               func.sum(case((unsplit, UsageRecord.cache_write_tokens), else_=0)))
        .where(*conds).group_by(UsageRecord.provider, UsageRecord.model, UsageRecord.step)
        .order_by(UsageRecord.provider, UsageRecord.model, UsageRecord.step)
    ).all()
    prices = {(p.source, p.model): p for p in s.scalars(select(AIPrice))}
    setting = s.get(CostSetting, firm_id)
    rate = setting.usd_twd if setting else DEFAULT_RATE
    out_rows = []
    totals = {"metered": [Decimal(0), Decimal(0)], "unmetered": [Decimal(0), Decimal(0)]}
    any_unsplit = any_unmetered = any_unpriced = False
    for src, mdl, stp, calls, failed, inp, outp, read, write, w5, w1, unsplit_write in rows:
        price = prices.get((src, mdl))
        billing = price.billing if price else (REGISTRY[src].billing if src in REGISTRY else None)
        usd = twd = None
        if price:
            usd = _q4((inp * price.input_usd + outp * price.output_usd + read * price.cache_read_usd
                       + w5 * price.cache_write_5m_usd + (w1 + unsplit_write) * price.cache_write_1h_usd) / MILLION)
            twd = _q2(usd * rate)
            totals[billing][0] += usd
            totals[billing][1] += twd
        else:
            any_unpriced = True
        any_unsplit = any_unsplit or unsplit_write > 0
        any_unmetered = any_unmetered or billing == "unmetered"
        out_rows.append({"source": src, "model": mdl, "step": stp, "calls": calls, "failed": failed, "input": inp,
                         "output": outp, "cache_read": read, "cache_write": write,
                         "usd": float(usd) if usd is not None else None,
                         "twd": float(twd) if twd is not None else None,
                         "billing": billing, "priced": price is not None})
    notes = ([UNMETERED_NOTE] if any_unmetered else []) + ([UNSPLIT_NOTE] if any_unsplit else []) \
        + ([UNPRICED_NOTE] if any_unpriced else [])
    if setting is None or setting.is_default:
        notes.append(DEFAULT_RATE_NOTE)
    return {
        "rows": out_rows,
        "totals": {k: {"usd": float(_q4(v[0])), "twd": float(_q2(v[1]))} for k, v in totals.items()},
        "notes": notes,
        "rate": {"usd_twd": float(rate), "rate_set_on": (setting.rate_set_on if setting
                                                          else datetime.now(TAIPEI).date()).isoformat(),
                 "is_default": setting.is_default if setting else True},
        "filters": _filters(s),
    }


def _filters(s: Session) -> dict:
    def distinct(col):
        return sorted(x for x in s.scalars(select(col).distinct()) if x)
    cases = s.execute(select(Case.id, Case.title).where(
        Case.id.in_(select(UsageRecord.case_id).where(UsageRecord.case_id.is_not(None)).distinct()))
        .order_by(Case.title)).all()
    return {"months": sorted(distinct(_month()), reverse=True), "steps": distinct(UsageRecord.step),
            "sources": distinct(UsageRecord.provider), "models": distinct(UsageRecord.model),
            "cases": [{"id": str(cid), "title": title} for cid, title in cases]}
