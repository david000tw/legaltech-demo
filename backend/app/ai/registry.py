"""AI 來源登錄表（設計 5.12）。新增來源＝一個實作（app/ai/ 底下）、一列登錄、一列預設單價（app/costs.py）。

檢查清單見 docs/ai-providers.md。程式其他地方只透過 get_provider 與 call_ai 使用 AI，不直接呼叫各家。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.ai.provider import AIProvider
from app.config import Settings


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    needs_key: bool
    billing: Literal["metered", "unmetered"]
    build: Callable[[Settings], AIProvider]


def _claude_code(settings: Settings) -> AIProvider:
    from app.ai.claude_code import ClaudeCodeLocalProvider

    return ClaudeCodeLocalProvider(settings.claude_cmd, settings.ai_model)


def _bedrock(settings: Settings) -> AIProvider:
    from app.ai.bedrock import BedrockProvider

    return BedrockProvider.build(settings)


REGISTRY: dict[str, ProviderInfo] = {
    "claude_code_local": ProviderInfo("claude_code_local", "本機 Claude Code（開發者訂閱）", False, "unmetered",
                                      _claude_code),
    "bedrock": ProviderInfo("bedrock", "AWS Bedrock（日本區）", True, "metered", _bedrock),
}
