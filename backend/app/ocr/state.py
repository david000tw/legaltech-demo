"""辨識的共用常數與判斷（計畫 2A，設計第 6 節）。"""
from __future__ import annotations

import math
from datetime import timedelta

UNREADABLE = "〔?〕"
AUTO_OCR_PAGE_LIMIT = 50
PAGE_IMAGE_DPI = 150
MAX_IMAGE_EDGE = 2000
A4_PX = (1240, 1754)
PROOF_BATCH = 5
PROOF_POLL_S = 30
OCR_AI_TIMEOUT_S = 120
OCR_STALE_AFTER = timedelta(minutes=10)
# 設計寫每頁約 10 秒；校對員實測每頁 15.5 秒，每批都要等它，所以先用 20 秒估，開始後改用實際速度
SECONDS_PER_PAGE_ESTIMATE = 20

# none＝檔案本身有文字；pending＝待辨識；read＝AI 已讀、校對中；done＝已辨識；failed＝辨識失敗
OCR_STATES = ("none", "pending", "read", "done", "failed")


def needs_check(pt) -> bool:
    """這個文字版本是否還要律師對照原圖。"""
    return pt is not None and bool(pt.needs_image_check) and not pt.verified_against_image


def estimate_minutes(pages: int) -> int:
    return max(1, math.ceil(pages * SECONDS_PER_PAGE_ESTIMATE / 60))
