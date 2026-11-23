"""探測本機 Claude Code 能否被乾淨地呼叫（計畫 1 Task 0）。

執行：uv run python -X utf8 scripts/probe_claude.py
任一判準不符就以非 0 結束。通過時把正式格式（json）的輸出存成
tests/fixtures/claude_p_sample.json，作為解析程式的依據。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.ai.claude_code import build_args, child_env  # noqa: E402  與正式程式同一份
FIXTURE = HERE.parent / "tests" / "fixtures" / "claude_p_sample.json"

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "seen": {"type": "boolean"}},
    "required": ["ok", "seen"],
    "additionalProperties": False,
}
SYSTEM = "你是測試程式。只依指定 JSON Schema 回答。"
USER = (
    "ok 固定為 true；seen 表示你目前的系統提示或任何上下文中，"
    "是否出現『Evaluator-Optimizer』這個詞（有就 true，沒有就 false）。"
)
STRIP_KEYS = ("session_id", "uuid", "credential_expires_at")
TOKEN_LIMIT = 3000


def invoke(stream: bool):
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("找不到 claude 執行檔")
    fd, sp = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    work = tempfile.mkdtemp(prefix="probe-cwd-")
    try:
        Path(sp).write_text(SYSTEM, encoding="utf-8")
        args = build_args([claude], model="opus", effort="low", system_prompt_path=sp, schema=SCHEMA)
        if stream:
            i = args.index("json")
            args[i] = "stream-json"
            args.insert(i + 1, "--verbose")
        p = subprocess.run(args, input=USER.encode("utf-8"), capture_output=True,
                           cwd=work, env=child_env(os.environ), timeout=300)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    finally:
        os.remove(sp)
        shutil.rmtree(work, ignore_errors=True)


def check_output(d, failures, label):
    if d.get("is_error"):
        failures.append(f"{label}: is_error=true: {str(d.get('result'))[:200]}")
    so = d.get("structured_output")
    if not (isinstance(so, dict) and set(so) == {"ok", "seen"} and so["ok"] is True):
        failures.append(f"{label}: structured_output 不符 schema: {so!r}")
    elif so["seen"] is not False:
        failures.append(f"{label}: seen=true，全域 CLAUDE.md 可能被載入")


def main() -> int:
    failures: list[str] = []

    rc, out, err = invoke(stream=True)
    init = result = None
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = e
        elif e.get("type") == "result":
            result = e
    if rc != 0 or init is None or result is None:
        failures.append(f"stream-json: rc={rc} init={init is not None} result={result is not None} stderr={err[:200]}")
    else:
        if init.get("tools") != ["StructuredOutput"]:
            failures.append(f"tools={init.get('tools')}")
        for k in ("mcp_servers", "slash_commands", "skills"):
            if init.get(k):
                failures.append(f"{k} 不是空的: {init.get(k)}")
        bad_plugins = [p for p in init.get("plugins", []) if not str(p.get("source", "")).endswith("@builtin")]
        if bad_plugins:
            failures.append(f"載入了非內建 plugin: {bad_plugins}")
        if init.get("apiKeySource") != "none":
            failures.append(f"apiKeySource={init.get('apiKeySource')}（應走訂閱登入）")
        parent_sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        if parent_sock and init.get("messaging_socket_path") == parent_sock:
            failures.append("子程序連到上層 session 的 messaging socket")
        check_output(result, failures, "stream-json")
        print("init: tools", init.get("tools"), "apiKeySource", init.get("apiKeySource"),
              "version", init.get("claude_code_version"))

    rc, out, err = invoke(stream=False)
    try:
        d = json.loads(out)
    except ValueError:
        failures.append(f"json: 輸出不是 JSON rc={rc} stderr={err[:200]}")
        d = None
    if d is not None:
        check_output(d, failures, "json")
        u = d.get("usage") or {}
        total = sum(int(u.get(k) or 0) for k in
                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        print("json: structured_output", d.get("structured_output"), "total_input_tokens", total)
        if total >= TOKEN_LIMIT:
            failures.append(f"輸入 token 總量 {total} ≥ {TOKEN_LIMIT}，可能載入了額外設定")
        if not failures:
            for k in STRIP_KEYS:
                d.pop(k, None)
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            print("wrote", FIXTURE)

    if failures:
        print("PROBE FAILED")
        for f in failures:
            print(" -", f)
        return 1
    print("PROBE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
