"""計畫 2A Task 4：校對員（PaddleOCR 指令工具）的啟動、等待、逾時與同時只跑一個。測試一律用假工具。"""
import sys
import time
from pathlib import Path

import pytest

from app.ocr import proofreader
from app.ocr.proofreader import ProofreaderUnavailable, finish_proofreader, kill_proofreader, start_proofreader
from tests.conftest import make_settings

FAKE = [sys.executable, str(Path(__file__).resolve().parent / "fakes" / "fake_paddle.py")]


@pytest.fixture(autouse=True)
def _slot_released():
    yield
    assert proofreader._SLOT.acquire(blocking=False), "測試結束時校對員名額沒有釋放"
    proofreader._SLOT.release()


def _imgs(tmp_path, n=2):
    out = []
    for i in range(n):
        p = tmp_path / f"p{i}.png"
        p.write_bytes(b"png")
        out.append(p)
    return out


def _run(tmp_path, n=2, *, expect=None, timeout_s=20, on_wait=lambda: None):
    out = tmp_path / "out.json"
    proc = start_proofreader(FAKE, _imgs(tmp_path, n), out)
    return proc, lambda: finish_proofreader(proc, out, expect if expect is not None else n, timeout_s, on_wait)


def test_test_settings_ignore_paddle_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PADDLE_CMD", '["python", "paddle_proofread.py"]')
    assert make_settings(tmp_path).paddle_cmd == []


def test_proofreader_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PADDLE", "ok")
    _, finish = _run(tmp_path)
    pages = finish()
    assert [p[0]["text"] for p in pages] == ["第1頁", "第2頁"]
    assert pages[0][0]["box"] == [0, 0, 10, 10]


@pytest.mark.parametrize("mode,expect", [("fail", None), ("badjson", None), ("ok", 3)])
def test_proofreader_failures(tmp_path, monkeypatch, mode, expect):
    monkeypatch.setenv("FAKE_PADDLE", mode)
    _, finish = _run(tmp_path, expect=expect)
    with pytest.raises(ProofreaderUnavailable):
        finish()


def test_proofreader_unavailable_without_cmd(tmp_path):
    assert start_proofreader([], _imgs(tmp_path), tmp_path / "o.json") is None
    with pytest.raises(ProofreaderUnavailable):
        finish_proofreader(None, tmp_path / "o.json", 2, 10, lambda: None)


def test_proofreader_timeout_kills_process(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PADDLE", "sleep")
    monkeypatch.setattr(proofreader, "PROOF_POLL_S", 0.1)
    proc, finish = _run(tmp_path, timeout_s=1)
    with pytest.raises(ProofreaderUnavailable):
        finish()
    assert proc.poll() is not None
    again = start_proofreader(FAKE, _imgs(tmp_path), tmp_path / "again.json")
    assert again is not None  # 名額已釋放
    kill_proofreader(again)


def test_proofreader_noisy_output_does_not_hang(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PADDLE", "noisy")
    _, finish = _run(tmp_path)
    t = time.monotonic()
    assert len(finish()) == 2
    assert time.monotonic() - t < 15


def test_proofreader_calls_on_wait_and_single_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PADDLE", "sleep")
    monkeypatch.setenv("FAKE_PADDLE_SLEEP", "0.8")
    monkeypatch.setattr(proofreader, "PROOF_POLL_S", 0.1)
    calls = []
    proc, finish = _run(tmp_path, on_wait=lambda: calls.append(1))
    assert proc is not None
    assert start_proofreader(FAKE, _imgs(tmp_path), tmp_path / "second.json") is None  # 同時只跑一個
    finish()
    assert len(calls) >= 3
    p2 = start_proofreader(FAKE, _imgs(tmp_path), tmp_path / "third.json")
    assert p2 is not None
    kill_proofreader(p2)
    kill_proofreader(p2)  # 重複呼叫不會多釋放名額
    assert p2.poll() is not None


def test_on_wait_exception_kills_process(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PADDLE", "sleep")
    monkeypatch.setattr(proofreader, "PROOF_POLL_S", 0.1)

    def stop():
        raise RuntimeError("工作已被接手")

    proc, finish = _run(tmp_path, on_wait=stop)
    with pytest.raises(RuntimeError):
        finish()
    assert proc.poll() is not None
    nxt = start_proofreader(FAKE, _imgs(tmp_path), tmp_path / "next.json")
    assert nxt is not None
    kill_proofreader(nxt)
