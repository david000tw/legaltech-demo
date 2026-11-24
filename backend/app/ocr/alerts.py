"""彙整一個文字版本的提醒：空白頁、規則檢查、與校對員比對、校對員未執行（設計 6.3）。"""
from __future__ import annotations

from app.ocr.compare import compare
from app.ocr.rules import rule_alerts

RED_KINDS = {"unreadable", "bad_date", "sum_mismatch"}


def _note(kind: str, message: str) -> dict:
    return {"kind": kind, "start": 0, "end": 0, "message": message, "proof_text": None, "box": None}


def compute_alerts(text: str, proof: list[dict] | None, proofreader_ran: bool) -> list[dict]:
    """proof：這一頁校對員讀到的 cells（None＝沒有校對結果）。"""
    if text.strip() == "":
        return [_note("blank", "空白頁？請確認")]
    alerts = rule_alerts(text)
    if proof is not None:
        alerts += compare(text, proof)
    alerts.sort(key=lambda a: a["start"])
    if not proofreader_ran:
        alerts.append(_note("proofreader_missing", "校對員未執行"))
    return alerts


def alert_color(kind: str) -> str:
    return "red" if kind in RED_KINDS else "yellow"
