"""AI 供應來源介面與使用限制。

- claude_code_local：開發者本機的 Claude Code（訂閱登入），只在 dev、只綁本機、只對內部測試事務所。
- bedrock：AWS Bedrock 日本區，已預留介面但尚未啟用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Firm, UsageRecord

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
ERROR_KINDS = {"not_logged_in", "usage_limit", "timeout", "invalid_output", "refusal", "other"}


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True)
class AIResult:
    data: dict
    usage: Usage
    provider: str
    model: str


class AIError(Exception):
    def __init__(self, kind: str, message: str = ""):
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown AIError kind {kind}")
        self.kind = kind
        self.message = message[:300]
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
    if settings.ai_provider == "bedrock":
        raise ProviderNotEnabled("Bedrock 尚未啟用")
    if settings.app_env != "dev":
        raise ProviderNotAllowed("本機 Claude Code 只能在 dev 環境使用")
    if settings.bind_host not in LOCAL_HOSTS:
        raise ProviderNotAllowed("本機 Claude Code 只能在只綁本機的伺服器使用")
    if not firm.is_internal_test:
        raise ProviderNotAllowed("本機 Claude Code 只能用於內部測試事務所")
    from app.ai.claude_code import ClaudeCodeLocalProvider

    return ClaudeCodeLocalProvider(settings.claude_cmd, settings.ai_model)


def record_usage(s: Session, firm_id: UUID, case_id: UUID | None, member_id: UUID | None, step: str,
                 result: AIResult) -> UsageRecord:
    rec = UsageRecord(firm_id=firm_id, case_id=case_id, member_id=member_id, step=step,
                      provider=result.provider, model=result.model,
                      input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
                      cache_read_tokens=result.usage.cache_read_tokens,
                      cache_write_tokens=result.usage.cache_write_tokens)
    s.add(rec)
    return rec
