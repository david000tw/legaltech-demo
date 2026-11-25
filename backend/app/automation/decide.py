"""自動流程的判斷規則（設計 6.2）：只讀資料庫，回傳該開始的動作或等待原因，不執行任何工作。

輪次的「排定」（Round.auto_next）從排定開始保留到該階段結束才清掉；這裡只看排定的輪次，
所以複製出來的輪次、上線前的輪次、從「拆完先停」切成全自動的輪次都不會被自動起草。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation.fingerprints import notes_input_fingerprint, opponent_fingerprint
from app.models import RUNNING_STATUSES, Case, Document, Round

CaseAction = Literal["evidence_notes"]
RoundAction = Literal["extract", "reextract", "draft", "resume_extract", "resume_draft"]
NOTES_WAIT = "等卷證說明完成"
NEW_BRIEF = "有新上傳的書狀"


@dataclass(frozen=True)
class Wait:
    """自動流程停下來在等什麼；code 給畫面選出口按鈕。hint：證據辨識用 confirm_ocr／resume_ocr／retry_failed；
    卷證說明該跑卻沒人啟動時是 start_notes（kick_round 會先去啟動它，不會一直空等）。"""
    code: Literal["opponent_ocr", "evidence_ocr", "evidence_notes_running", "new_brief", "blocked"]
    message: str
    document_id: UUID | None = None
    page_id: UUID | None = None
    hint: str | None = None


def mode_of(s: Session, case: Case) -> str:
    """案件的自動化程度。所有自動流程的判斷都經過這裡（測試預設換成 manual，見 tests/conftest.py）。"""
    return case.automation


def _job_running(s: Session, case_id: UUID, step: str) -> bool:
    from app.pipeline.jobs import _stale, latest_job
    job = latest_job(s, case_id, None, step)
    return job is not None and job.status == "running" and not _stale(job)


def evidence_wait(s: Session, case_id: UUID) -> Wait | None:
    """我方證據還有頁沒有文字：起草與卷證說明都要等。依「辨識失敗 → 等確認 → 中斷 → 辨識中」的順序挑一份說明。"""
    from app.pipeline.jobs import _stale, latest_job
    from app.pipeline.requests import evidence_pages
    pending = [ep for ep in evidence_pages(s, case_id) if ep.text is None and ep.page.ocr_state != "none"]
    if not pending:
        return None
    docs: dict[UUID, tuple[str, int, bool]] = {}  # 文件 → (證物號, 待處理頁數, 有沒有辨識失敗的頁)
    for ep in pending:
        label, n, failed = docs.get(ep.page.document_id, (ep.exhibit, 0, False))
        docs[ep.page.document_id] = (label, n + 1, failed or ep.page.ocr_state == "failed")
    states = []
    for doc_id, (label, n, failed) in docs.items():
        job = latest_job(s, case_id, None, "ocr", doc_id)
        if failed:
            states.append((0, Wait("evidence_ocr", f"等證據處理完：{label} 有頁辨識失敗", document_id=doc_id,
                                   hint="retry_failed")))
        elif job is not None and job.status == "waiting":
            states.append((1, Wait("evidence_ocr", f"等證據處理完：{label} 共 {n} 頁掃描檔，等你確認是否辨識",
                                   document_id=doc_id, hint="confirm_ocr")))
        elif job is None or job.status != "running" or _stale(job):
            states.append((2, Wait("evidence_ocr", f"等證據處理完：{label} 還有 {n} 頁沒辨識", document_id=doc_id,
                                   hint="resume_ocr")))
        else:
            states.append((3, Wait("evidence_ocr", f"等證據處理完：{label} 辨識中，還有 {n} 頁", document_id=doc_id)))
    return min(states, key=lambda x: x[0])[1]


def next_case_action(s: Session, case_id: UUID) -> CaseAction | None:
    """證據側：卷證說明。同一份輸入只自動跑一次（不論上次成功、失敗或 AI 沒寫）；大事記不自動跑。"""
    case = s.get(Case, case_id)
    if mode_of(s, case) == "manual" or evidence_wait(s, case_id) is not None:
        return None
    fp = notes_input_fingerprint(s, case_id)
    if not fp or fp == case.notes_auto_fingerprint or _job_running(s, case_id, "evidence_notes"):
        return None
    return "evidence_notes"


def _opponent_problem(s: Session, round_id: UUID) -> Wait | None:
    from app.ocr.gates import opponent_ocr_problem
    from app.pipeline.claims import opponent_pages
    problem = opponent_ocr_problem(s, round_id)
    if problem is None:
        return None
    first = next((op.page.id for op in opponent_pages(s, round_id)
                  if op.text is None or op.page.ocr_state == "failed"
                  or (op.page.blank_confirmed_at is None
                      and any(a.get("kind") == "blank" for a in op.text.alerts or []))), None)
    return Wait("opponent_ocr", problem, page_id=first)


def _new_brief(s: Session, rnd: Round) -> bool:
    """拆解之後又有對方書狀加入。指紋是 NULL（複製來的、上線前的輪次）一律當作沒有。"""
    return rnd.extract_fingerprint is not None and rnd.extract_fingerprint != opponent_fingerprint(s, rnd.id)


def next_round_action(s: Session, round_id: UUID) -> RoundAction | Wait | None:
    from app.pipeline.run import _stale, reset_needs_confirm
    rnd = s.get(Round, round_id)
    if mode_of(s, s.get(Case, rnd.case_id)) == "manual":
        return None
    if rnd.auto_next is None:
        return Wait("blocked", rnd.auto_note) if rnd.auto_note else None
    if rnd.status in RUNNING_STATUSES:
        armed = {"extract": "extracting", "draft": "drafting"}[rnd.auto_next]
        if rnd.status != armed or not _stale(rnd) or rnd.auto_retries >= 1:
            return None
        return "resume_extract" if rnd.auto_next == "extract" else "resume_draft"
    if rnd.status == "created":
        if rnd.auto_next != "extract" or rnd.brief_kind == "complaint":
            return None
        if s.scalar(select(Document.id).where(Document.round_id == round_id,
                                              Document.kind == "opponent_brief").limit(1)) is None:
            return None
        return _opponent_problem(s, round_id) or "extract"
    if rnd.status != "extracted":
        return None  # 已起草、失敗：自動流程不動
    if _new_brief(s, rnd):
        return _opponent_problem(s, round_id) or (
            Wait("new_brief", NEW_BRIEF) if reset_needs_confirm(s, round_id) else "reextract")
    if rnd.auto_next != "draft":
        return None
    wait = evidence_wait(s, rnd.case_id)
    if wait is not None:
        return wait
    if _job_running(s, rnd.case_id, "evidence_notes"):
        return Wait("evidence_notes_running", NOTES_WAIT)
    if next_case_action(s, rnd.case_id) == "evidence_notes":  # 該跑卻還沒人啟動（例如證據是手動模式時上傳的）
        return Wait("evidence_notes_running", NOTES_WAIT, hint="start_notes")
    return "draft"


def automation_view(s: Session, round_id: UUID) -> dict:
    """輪次卡片用：自動化程度、排定的下一步、正在跑哪一段、在等什麼。"""
    from app.pipeline.run import _stale
    rnd = s.get(Round, round_id)
    action = next_round_action(s, round_id)
    running = ({"extracting": "extract", "drafting": "draft"}[rnd.status]
               if rnd.status in RUNNING_STATUSES and not _stale(rnd) else None)
    wait = None
    if isinstance(action, Wait):
        wait = {k: (str(v) if isinstance(v, UUID) else v) for k, v in asdict(action).items()}
    return {"mode": mode_of(s, s.get(Case, rnd.case_id)), "auto_next": rnd.auto_next, "running": running,
            "wait": wait, "failed_stage": rnd.failed_stage if rnd.status == "failed" else None}
