"""自動流程的判斷規則（設計 6.2）：只讀資料庫，回傳該開始的動作或等待原因，不執行任何工作。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Case


def mode_of(s: Session, case: Case) -> str:
    """案件的自動化程度。所有自動流程的判斷都經過這裡（測試預設換成 manual，見 tests/conftest.py）。"""
    return case.automation
