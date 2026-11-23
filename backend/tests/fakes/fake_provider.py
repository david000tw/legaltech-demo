"""測試用的 AI 供應來源：依步驟名回傳預設 JSON，或丟出預設的例外；記錄每次呼叫。"""
from __future__ import annotations

from collections.abc import Callable

from app.ai.provider import AIResult, Usage


class FakeProvider:
    def __init__(self, responses: dict[str, dict | Exception | Callable[[dict], dict]]):
        self.responses = responses
        self.calls: list[dict] = []

    def run(self, *, step: str, system: str, user: str, schema: dict, effort: str,
            timeout_s: int = 600) -> AIResult:
        call = {"step": step, "system": system, "user": user, "schema": schema, "effort": effort}
        self.calls.append(call)
        r = self.responses[step]
        if isinstance(r, Exception):
            raise r
        data = r(call) if callable(r) else r
        return AIResult(data=data, usage=Usage(1, 1, 1, 1), provider="fake", model="fake")

    def steps(self) -> list[str]:
        return [c["step"] for c in self.calls]
