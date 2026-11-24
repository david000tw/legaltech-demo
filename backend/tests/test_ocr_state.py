"""辨識狀態的共用常數與判斷。"""
from types import SimpleNamespace

from app.ocr.state import OCR_STATES, estimate_minutes, needs_check


def test_states():
    assert OCR_STATES == ("none", "pending", "read", "done", "failed")


def test_needs_check():
    assert needs_check(SimpleNamespace(needs_image_check=False, verified_against_image=False)) is False
    assert needs_check(SimpleNamespace(needs_image_check=True, verified_against_image=False)) is True
    assert needs_check(SimpleNamespace(needs_image_check=True, verified_against_image=True)) is False
    assert needs_check(None) is False


def test_estimate_minutes():
    assert [estimate_minutes(n) for n in (1, 3, 4, 30, 50)] == [1, 1, 2, 10, 17]
