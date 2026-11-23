import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.ai.claude_code import ClaudeCodeLocalProvider, build_args, child_env
from app.ai.provider import (
    AIError, AIResult, ProviderNotAllowed, ProviderNotEnabled, Usage, check_startup, get_provider,
    record_usage,
)
from app.ai.tokens import estimate_tokens
from app.db import tenant_session
from app.main import create_app
from app.models import Firm, UsageRecord
from tests.conftest import make_settings
from tests.helpers import create_firm

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_claude.py"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "fake_log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def run(mode: str, *, user: str = "請回答", timeout_s: int = 60):
        monkeypatch.setenv("FAKE_MODE", mode)
        p = ClaudeCodeLocalProvider([sys.executable, str(FAKE)], "opus")
        return p.run(step="test", system="系統指示：只回 JSON", user=user, schema=SCHEMA, effort="low",
                     timeout_s=timeout_s)

    def calls() -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    run.calls = calls
    return run


def test_parses_structured_output_and_usage(fake):
    r = fake("ok")
    assert isinstance(r, AIResult)
    assert r.data == {"ok": True, "seen": False}
    assert r.provider == "claude_code_local" and r.model == "opus"
    sample = json.loads((FAKE.parent.parent / "fixtures" / "claude_p_sample.json").read_text(encoding="utf-8"))
    u = sample["usage"]
    assert r.usage.total_input == (u["input_tokens"] + u["cache_read_input_tokens"]
                                   + u["cache_creation_input_tokens"])
    assert r.usage.output_tokens == u["output_tokens"]


def test_chinese_roundtrip(fake):
    r = fake("echo", user="臺灣臺中地方法院113年度中簡字第3946號")
    assert r.data == {"echo": "臺灣臺中地方法院113年度中簡字第3946號"}
    assert fake.calls()[0]["system_prompt"] == "系統指示：只回 JSON"


def test_args_fixed_and_tools_last(fake):
    fake("ok")
    argv = fake.calls()[0]["argv"]
    assert argv[0] == "-p"
    for flag in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[-2:] == ["--tools", ""]
    sp = argv[argv.index("--system-prompt-file") + 1]
    assert not os.path.exists(sp)


def test_build_args_matches_contract():
    args = build_args(["claude"], model="opus", effort="high", system_prompt_path="p.md", schema=SCHEMA)
    assert args == ["claude", "-p", "--output-format", "json", "--model", "opus", "--effort", "high",
                    "--safe-mode", "--setting-sources", "", "--strict-mcp-config",
                    "--disable-slash-commands", "--no-session-persistence",
                    "--system-prompt-file", "p.md", "--json-schema", json.dumps(SCHEMA), "--tools", ""]


def test_child_env_strips_claude_vars(fake, monkeypatch):
    for k, v in {"CLAUDECODE": "1", "CLAUDE_EFFORT": "max", "CLAUDE_CODE_SESSION_ID": "x",
                 "ANTHROPIC_API_KEY": "x", "ANTHROPIC_AUTH_TOKEN": "x", "CLAUDE_CONFIG_DIR": "d"}.items():
        monkeypatch.setenv(k, v)
    fake("ok")
    keys = {k.upper() for k in fake.calls()[0]["env_keys"]}
    assert "CLAUDE_CONFIG_DIR" in keys
    for k in ("CLAUDECODE", "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        assert k not in keys
    assert child_env({"claude_lower": "1", "PATH": "p"}) == {"PATH": "p"}


def test_cwd_is_fresh_empty_dir(fake):
    fake("ok")
    fake("ok")
    c1, c2 = fake.calls()
    assert c1["cwd_listing"] == [] and c2["cwd_listing"] == []
    assert c1["cwd"] != c2["cwd"] and c1["cwd"] != os.getcwd()
    assert not os.path.exists(c1["cwd"])


def test_invalid_json_retries_then_raises_invalid_output(fake):
    with pytest.raises(AIError) as e:
        fake("badjson")
    assert e.value.kind == "invalid_output"
    assert len(fake.calls()) == 2
    assert "這不是 JSON" not in e.value.message


def test_missing_structured_output_is_invalid_output(fake):
    with pytest.raises(AIError) as e:
        fake("no_structured")
    assert e.value.kind == "invalid_output" and len(fake.calls()) == 2
    assert "我用文字回答了" not in e.value.message


def test_refusal_retried_once_then_raises(fake):
    with pytest.raises(AIError) as e:
        fake("refusal")
    assert e.value.kind == "refusal"
    assert len(fake.calls()) == 2


def test_usage_limit_detected(fake):
    with pytest.raises(AIError) as e:
        fake("usage_limit")
    assert e.value.kind == "usage_limit"
    assert "session limit" in e.value.message
    assert len(fake.calls()) == 1


def test_not_logged_in_detected(fake):
    with pytest.raises(AIError) as e:
        fake("not_logged_in")
    assert e.value.kind == "not_logged_in"


def test_timeout_raises_timeout(fake):
    with pytest.raises(AIError) as e:
        fake("sleep", timeout_s=2)
    assert e.value.kind == "timeout"


def test_missing_executable_is_other():
    p = ClaudeCodeLocalProvider(["definitely-not-a-real-claude-binary-xyz"], "opus")
    with pytest.raises(AIError) as e:
        p.run(step="t", system="s", user="u", schema=SCHEMA, effort="low")
    assert e.value.kind == "other" and "找不到 claude" in e.value.message


def test_provider_not_allowed_outside_dev(tmp_path):
    firm = Firm(name="A", is_internal_test=True)
    with pytest.raises(ProviderNotAllowed):
        get_provider(make_settings(tmp_path, app_env="pilot"), firm)
    with pytest.raises(ProviderNotAllowed):
        get_provider(make_settings(tmp_path, bind_host="0.0.0.0"), firm)
    assert isinstance(get_provider(make_settings(tmp_path), firm), ClaudeCodeLocalProvider)


def test_provider_not_allowed_for_non_internal_firm(tmp_path):
    with pytest.raises(ProviderNotAllowed):
        get_provider(make_settings(tmp_path), Firm(name="真事務所", is_internal_test=False))


def test_startup_refuses_pilot_with_local_provider(tmp_path):
    with pytest.raises(ProviderNotAllowed):
        check_startup(make_settings(tmp_path, app_env="pilot", ai_provider="claude_code_local"))
    with pytest.raises(ProviderNotAllowed):
        create_app(make_settings(tmp_path, app_env="prod"))
    check_startup(make_settings(tmp_path, app_env="pilot", ai_provider="bedrock"))


def test_bedrock_not_enabled(tmp_path):
    with pytest.raises(ProviderNotEnabled):
        get_provider(make_settings(tmp_path, ai_provider="bedrock"), Firm(name="A", is_internal_test=True))


def test_record_usage_stores_no_content(db):
    firm_id = create_firm("甲事務所")
    result = AIResult(data={"secret": "對造書狀內容"}, usage=Usage(1, 2, 3, 4), provider="claude_code_local",
                      model="opus")
    with tenant_session(firm_id) as s:
        rec = record_usage(s, firm_id, None, None, "draft", result)
        s.flush()
        assert (rec.input_tokens, rec.output_tokens, rec.cache_read_tokens, rec.cache_write_tokens) == (1, 2, 3, 4)
    cols = {c.key for c in inspect(UsageRecord).columns}
    assert cols == {"id", "firm_id", "case_id", "member_id", "step", "provider", "model", "input_tokens",
                    "output_tokens", "cache_read_tokens", "cache_write_tokens", "created_at"}


def test_estimate_tokens():
    assert estimate_tokens("一二三四五六七八九十") == 13
    assert estimate_tokens("") == 0


def test_cli_error_keeps_stderr_and_does_not_retry(fake):
    with pytest.raises(AIError) as e:
        fake("cli_error")
    assert e.value.kind == "other" and "unknown option" in e.value.message
    assert len(fake.calls()) == 1
