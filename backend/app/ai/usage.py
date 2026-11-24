"""所有 AI 呼叫的唯一入口：呼叫 provider，並在獨立交易記下每一次嘗試的用量（設計 5.12）。

- 成功：provider 內部重試的前幾次各記一列 failed，最後一次記一列 ok；
- 失敗（AIError）：每次失敗嘗試各記一列 failed（沒有用量資訊就記一列全 0），再原樣丟出。
獨立交易的用意：呼叫之後的步驟失敗、整筆交易復原時，花掉的用量仍留著。
"""
from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.ai.provider import AIError, AIProvider, AIResult, Usage
from app.db import tenant_session
from app.models import UsageRecord

ZERO = Usage(0, 0, 0, 0)


def _row(firm_id: UUID, case_id: UUID | None, member_id: UUID | None, step: str, provider: str, model: str,
         usage: Usage, result: str) -> UsageRecord:
    return UsageRecord(firm_id=firm_id, case_id=case_id, member_id=member_id, step=step, provider=provider,
                       model=model, result=result, input_tokens=usage.input_tokens,
                       output_tokens=usage.output_tokens, cache_read_tokens=usage.cache_read_tokens,
                       cache_write_tokens=usage.cache_write_tokens, cache_write_5m_tokens=usage.cache_write_5m,
                       cache_write_1h_tokens=usage.cache_write_1h)


def call_ai(provider: AIProvider, *, firm_id: UUID, case_id: UUID | None, member_id: UUID | None, step: str,
            system: str, user: str, schema: dict, effort: str, timeout_s: int = 600,
            images: Sequence[bytes] = (), model: str | None = None) -> AIResult:
    def record(rows: list[tuple[str, str, Usage, str]]) -> None:
        with tenant_session(firm_id) as s:
            s.add_all(_row(firm_id, case_id, member_id, step, name, model, u, res) for name, model, u, res in rows)

    try:
        result = provider.run(step=step, system=system, user=user, schema=schema, effort=effort, timeout_s=timeout_s,
                              images=images, model=model)
    except AIError as e:
        name, used = getattr(provider, "name", "unknown"), model or getattr(provider, "model", "unknown")
        record([(name, used, u, "failed") for u in (e.attempts or (ZERO,))])
        raise
    record([(result.provider, result.model, u, "failed") for u in result.retried]
           + [(result.provider, result.model, result.usage, "ok")])
    return result
