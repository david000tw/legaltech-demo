"""校對員：在獨立環境執行 PaddleOCR 指令工具（tools/paddle/paddle_proofread.py），與 AI 互相核對。

- 同一個後端程序同時只跑一個（PaddleOCR 吃 CPU 與記憶體）；拿不到名額就當作這批沒有校對員；
- 輸出一律寫檔，stdout、stderr 丟棄，避免管線塞滿卡住；
- 等待時定期呼叫 on_wait（更新工作心跳）；任何離開方式都確保程序已結束、名額已釋放。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from app.ocr.state import PROOF_POLL_S

_SLOT = threading.BoundedSemaphore(1)


class ProofreaderUnavailable(Exception):
    """校對員沒有執行或結果不能用；該批頁面改加「校對員未執行」。"""


def _release(proc) -> None:
    if proc is not None and getattr(proc, "_holds_slot", False):
        proc._holds_slot = False
        _SLOT.release()


def _stop(proc) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def start_proofreader(cmd: Sequence[str], image_paths: Sequence[Path | str], out_path: Path | str,
                      wait_s: float = 0, on_wait: Callable[[], None] | None = None) -> subprocess.Popen | None:
    """拿到名額就啟動；wait_s > 0 時名額被占用會等（每 PROOF_POLL_S 秒呼叫 on_wait），等不到才回 None。"""
    if not cmd:
        return None
    deadline = time.monotonic() + wait_s
    while not _SLOT.acquire(timeout=max(0.0, min(PROOF_POLL_S, deadline - time.monotonic()))):
        if time.monotonic() >= deadline:
            return None
        if on_wait is not None:
            on_wait()
    try:
        proc = subprocess.Popen([*cmd, str(out_path), *(str(p) for p in image_paths)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env={**os.environ, "PYTHONUTF8": "1"})
    except OSError:
        _SLOT.release()
        return None
    proc._holds_slot = True
    return proc


def kill_proofreader(proc: subprocess.Popen | None) -> None:
    """呼叫端在例外時使用：結束程序並釋放名額（可重複呼叫）。"""
    if proc is None:
        return
    try:
        _stop(proc)
    finally:
        _release(proc)


def _read(out_path: Path | str, n: int) -> list[list[dict]]:
    try:
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
        pages = [p["cells"] for p in data]
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise ProofreaderUnavailable(f"校對結果無法讀取：{e}") from e
    if len(pages) != n or not all(isinstance(c, list) for c in pages):
        raise ProofreaderUnavailable(f"校對結果頁數不符：{len(pages)}／{n}")
    return [[{"text": str(c.get("text", "")), "score": float(c.get("score") or 0), "box": c.get("box")}
             for c in cells if isinstance(c, dict)] for cells in pages]


def finish_proofreader(proc: subprocess.Popen | None, out_path: Path | str, n: int, timeout_s: float,
                       on_wait: Callable[[], None]) -> list[list[dict]]:
    """等校對員結束並讀結果（每頁一組 cells）。"""
    if proc is None:
        raise ProofreaderUnavailable("校對員未執行")
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                code = proc.wait(timeout=max(0.0, min(PROOF_POLL_S, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                on_wait()
                if time.monotonic() >= deadline:
                    raise ProofreaderUnavailable(f"校對員超過 {timeout_s} 秒沒有完成")
        if code != 0:
            raise ProofreaderUnavailable(f"校對員異常結束（結束碼 {code}）")
        return _read(out_path, n)
    finally:
        kill_proofreader(proc)
