"""以子程序呼叫開發者本機的 Claude Code（claude -p），只用結構化輸出。

參數、環境變數與工作目錄的處理方式已在計畫 1 Task 0 實測（scripts/probe_claude.py）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping

from app.ai.provider import AIError, AIResult, Usage

PROVIDER_NAME = "claude_code_local"
_STRIP_EXACT = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}


def child_env(base: Mapping[str, str]) -> dict[str, str]:
    """剔除 CLAUDE* 變數（保留 CLAUDE_CONFIG_DIR）與 API 金鑰，避免子程序被當成巢狀 session 或改走 API 計費。"""
    out = {}
    for k, v in base.items():
        up = k.upper()
        if up.startswith("CLAUDE") and up != "CLAUDE_CONFIG_DIR":
            continue
        if up in _STRIP_EXACT:
            continue
        out[k] = v
    return out


def build_args(cmd: list[str], *, model: str, effort: str, system_prompt_path: str, schema: dict) -> list[str]:
    # --tools 會吃掉後面所有不以 -- 開頭的參數，所以必須放最後。
    return [*cmd, "-p", "--output-format", "json", "--model", model, "--effort", effort,
            "--safe-mode", "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence",
            "--system-prompt-file", system_prompt_path,
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--tools", ""]


def classify_failure(payload: dict | None, stderr: str) -> AIError:
    """錯誤訊息字串未經實證，以關鍵字寬鬆分類；is_error 時的 result 是錯誤訊息，不是模型輸出。"""
    result = str((payload or {}).get("result") or "")
    text = f"{result}\n{stderr}".strip()
    low = text.lower()
    if any(w in low for w in ("not logged in", "log in", "login", "credential")):
        kind = "not_logged_in"
    elif (payload or {}).get("api_error_status") == 429 or (
            "limit" in low and any(w in low for w in ("session", "weekly", "usage", "reset"))):
        kind = "usage_limit"
    else:
        kind = "other"
    return AIError(kind, text)


def _usage(payload: dict | None) -> Usage:
    u = (payload or {}).get("usage") or {}
    split = u.get("cache_creation")  # 有回報時分 5 分鐘與 1 小時兩種快取寫入，單價不同
    split = split if isinstance(split, dict) else None
    return Usage(input_tokens=int(u.get("input_tokens") or 0),
                 output_tokens=int(u.get("output_tokens") or 0),
                 cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
                 cache_write_tokens=int(u.get("cache_creation_input_tokens") or 0),
                 cache_write_5m=int(split.get("ephemeral_5m_input_tokens") or 0) if split else None,
                 cache_write_1h=int(split.get("ephemeral_1h_input_tokens") or 0) if split else None)


def _with_attempts(e: AIError, attempts: list[Usage]) -> AIError:
    return AIError(e.kind, e.message, attempts=tuple(attempts))


class ClaudeCodeLocalProvider:
    name = PROVIDER_NAME

    def __init__(self, cmd: list[str], model: str):
        self.cmd = list(cmd)
        self.model = model

    def _call(self, *, system: str, user: str, schema: dict, effort: str,
              timeout_s: int) -> tuple[dict | None, str, int]:
        fd, sp_path = tempfile.mkstemp(suffix=".md", prefix="sysprompt-")
        os.close(fd)
        work = tempfile.mkdtemp(prefix="claude-cwd-")
        try:
            with open(sp_path, "w", encoding="utf-8") as f:
                f.write(system)
            args = build_args(self.cmd, model=self.model, effort=effort, system_prompt_path=sp_path,
                              schema=schema)
            try:
                p = subprocess.run(args, input=user.encode("utf-8"), capture_output=True, cwd=work,
                                   env=child_env(os.environ), timeout=timeout_s)
            except FileNotFoundError:
                raise AIError("other", "找不到 claude 執行檔，請確認 Claude Code 已安裝並在 PATH 中")
            except subprocess.TimeoutExpired:
                raise AIError("timeout", f"Claude Code 超過 {timeout_s} 秒沒有回應")
            stderr = p.stderr.decode("utf-8", "replace")
            try:
                payload = json.loads(p.stdout.decode("utf-8", "replace"))
            except ValueError:
                payload = None
            return (payload if isinstance(payload, dict) else None), stderr, p.returncode
        finally:
            try:
                os.remove(sp_path)
            except OSError:
                pass
            shutil.rmtree(work, ignore_errors=True)

    def run(self, *, step: str, system: str, user: str, schema: dict, effort: str,
            timeout_s: int = 600) -> AIResult:
        last = AIError("other", "未知錯誤")
        attempts: list[Usage] = []  # 每次失敗嘗試的用量（輸出不合格也已經花了 token）
        for _ in range(2):  # 輸出不合法或拒答時重試一次
            try:
                payload, stderr, code = self._call(system=system, user=user, schema=schema, effort=effort,
                                                   timeout_s=timeout_s)
            except AIError as e:  # 逾時、找不到執行檔：這次沒有用量資訊
                raise _with_attempts(e, [*attempts, _usage(None)]) from e
            if payload is None and code != 0:  # CLI 本身出錯（參數不認得、當掉）：保留原始訊息，不重試
                e = classify_failure(None, stderr.strip() or f"Claude Code 異常結束（結束碼 {code}）")
                raise _with_attempts(e, [*attempts, _usage(None)])
            attempts.append(_usage(payload))
            if payload is None:
                last = AIError("invalid_output", f"步驟 {step}：Claude Code 輸出不是 JSON")
                continue
            if payload.get("is_error"):
                raise _with_attempts(classify_failure(payload, stderr), attempts)
            if payload.get("stop_reason") == "refusal":
                last = AIError("refusal", f"步驟 {step}：模型拒絕回答")
                continue
            data = payload.get("structured_output")
            if not isinstance(data, dict):
                last = AIError("invalid_output", f"步驟 {step}：缺少結構化輸出")
                continue
            return AIResult(data=data, usage=attempts[-1], provider=PROVIDER_NAME, model=self.model,
                            retried=tuple(attempts[:-1]))
        raise _with_attempts(last, attempts)
