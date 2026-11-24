"""AI 供應來源介面與使用限制。

- claude_code_local：開發者本機的 Claude Code（訂閱登入），只在 dev、只綁本機、只對內部測試事務所。
- bedrock：AWS Bedrock 日本區，已預留介面但尚未啟用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.models import Firm

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ERROR_KINDS = {"not_logged_in", "usage_limit", "timeout", "invalid_output", "refusal", "other"}


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int  # 快取寫入合計
    cache_write_5m: int | None = None  # 來源有分開回報 5 分鐘與 1 小時快取寫入時才有值
    cache_write_1h: int | None = None

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True)
class AIResult:
    data: dict
    usage: Usage
    provider: str
    model: str
    retried: tuple[Usage, ...] = ()  # provider 內部重試時，前幾次（失敗）嘗試的用量


class AIError(Exception):
    def __init__(self, kind: str, message: str = "", attempts: tuple[Usage, ...] = ()):
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown AIError kind {kind}")
        self.kind = kind
        self.message = message[:300]
        self.attempts = tuple(attempts)  # 每次失敗嘗試各一筆用量；取不到用量的嘗試是全 0
        super().__init__(f"{kind}: {self.message}")


class ProviderNotAllowed(Exception):
    pass


class ProviderNotEnabled(Exception):
    pass


class AIProvider(Protocol):
    def run(self, *, step: str, system: str, user: str, schema: dict, effort: str,
            timeout_s: int = 600) -> AIResult: ...


def check_startup(settings: Settings) -> None:
    if settings.app_env in ("pilot", "prod") and settings.ai_provider != "bedrock":
        raise ProviderNotAllowed(f"APP_ENV={settings.app_env} 時 AI_PROVIDER 必須是 bedrock")


def get_provider(settings: Settings, firm: Firm) -> AIProvider:
    """依 AI 來源登錄表（app/ai/registry.py）建立；本機 Claude Code 的使用限制在這裡檢查。"""
    from app.ai.registry import REGISTRY

    if settings.ai_provider != "claude_code_local":
        return REGISTRY[settings.ai_provider].build(settings)
    if settings.app_env != "dev":
        raise ProviderNotAllowed("本機 Claude Code 只能在 dev 環境使用")
    if settings.bind_host not in LOCAL_HOSTS:
        raise ProviderNotAllowed("本機 Claude Code 只能在只綁本機的伺服器使用")
    if not firm.is_internal_test:
        raise ProviderNotAllowed("本機 Claude Code 只能用於內部測試事務所")
    return REGISTRY["claude_code_local"].build(settings)
