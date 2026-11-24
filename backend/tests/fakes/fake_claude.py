"""假的 claude 執行檔：依 FAKE_MODE 輸出不同內容，並把收到的呼叫寫到 FAKE_LOG（每次一行 JSON）。

輸出一律寫入 sys.stdout.buffer（UTF-8 bytes），不受 Windows cp950 影響。
"""
import json
import os
import sys
import time
from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "claude_p_sample.json"


def result(**kw) -> dict:
    base = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "tool_use",
            "api_error_status": None, "result": "", "structured_output": None,
            "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 7,
                      "cache_creation_input_tokens": 11}}
    base.update(kw)
    return base


def main() -> None:
    stdin = sys.stdin.buffer.read().decode("utf-8")
    argv = sys.argv[1:]
    stream = "--input-format" in argv and argv[argv.index("--input-format") + 1] == "stream-json"
    images = 0
    if stream:
        msg = json.loads(stdin.splitlines()[0])
        images = sum(1 for c in msg["message"]["content"] if c.get("type") == "image")
    log = os.environ.get("FAKE_LOG")
    if log:
        sp = argv[argv.index("--system-prompt-file") + 1] if "--system-prompt-file" in argv else None
        entry = {
            "argv": argv, "cwd": os.getcwd(), "cwd_listing": os.listdir(os.getcwd()),
            "env_keys": sorted(os.environ), "stdin": stdin, "stdin_images": images,
            "system_prompt": Path(sp).read_text(encoding="utf-8") if sp and Path(sp).exists() else None,
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "ok":
        out = SAMPLE.read_text(encoding="utf-8")
    elif mode == "echo":
        out = json.dumps(result(structured_output={"echo": stdin}), ensure_ascii=False)
    elif mode == "badjson":
        out = "這不是 JSON"
    elif mode == "no_structured":
        out = json.dumps(result(result="我用文字回答了"), ensure_ascii=False)
    elif mode == "refusal":
        out = json.dumps(result(stop_reason="refusal"), ensure_ascii=False)
    elif mode == "usage_limit":
        out = json.dumps(result(is_error=True, subtype="error_during_execution",
                                result="You've hit your session limit · resets 3pm"), ensure_ascii=False)
    elif mode == "not_logged_in":
        out = json.dumps(result(is_error=True, result="Not logged in · Please run /login"), ensure_ascii=False)
    elif mode == "cli_error":
        sys.stderr.buffer.write("error: unknown option '--safe-mode'\n".encode("utf-8"))
        raise SystemExit(1)
    elif mode == "stream_result_only":  # stream-json 沒給 structured_output，只有 result 字串
        out = json.dumps(result(result=json.dumps({"ok": True})), ensure_ascii=False)
    elif mode == "sleep":
        time.sleep(30)
        out = "{}"
    else:
        raise SystemExit(f"unknown FAKE_MODE {mode}")
    if stream:  # 一行一個事件：先 system，再 result
        try:
            out = json.dumps(json.loads(out), ensure_ascii=False)
        except ValueError:
            pass
        out = json.dumps({"type": "system"}) + "\n" + out + "\n"
    sys.stdout.buffer.write(out.encode("utf-8"))


if __name__ == "__main__":
    main()
