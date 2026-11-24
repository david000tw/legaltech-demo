"""假的校對員指令工具：paddle_proofread.py 的介面（<out.json> <img1.png> [...]），依 FAKE_PADDLE 決定行為。

ok：每張圖一個 cell「第N頁」；fail：結束碼 1；badjson：寫壞掉的 JSON；
sleep：先睡 FAKE_PADDLE_SLEEP 秒（預設 30）再照 ok 寫；noisy：先往 stdout、stderr 各寫 1 MB 再照 ok 寫。
FAKE_PADDLE_CELLS（JSON）可指定每頁的 cells。
"""
import json
import os
import sys
import time
from pathlib import Path


def main() -> None:
    out, *images = sys.argv[1:]
    mode = os.environ.get("FAKE_PADDLE", "ok")
    if mode == "fail":
        raise SystemExit(1)
    if mode == "badjson":
        Path(out).write_text("不是 JSON", encoding="utf-8")
        return
    if mode == "sleep":
        time.sleep(float(os.environ.get("FAKE_PADDLE_SLEEP", "30")))
    if mode == "noisy":
        sys.stdout.write("x" * 1024 * 1024)
        sys.stdout.flush()
        sys.stderr.write("y" * 1024 * 1024)
        sys.stderr.flush()
    preset = os.environ.get("FAKE_PADDLE_CELLS")
    if preset:
        pages = [{"cells": c} for c in json.loads(preset)]
    else:
        pages = [{"cells": [{"text": f"第{i}頁", "score": 0.99, "box": [0, 0, 10, 10]}]}
                 for i in range(1, len(images) + 1)]
    Path(out).write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
