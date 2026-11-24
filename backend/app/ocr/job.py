"""辨識工作（計畫 2A，設計 6.2）：一份文件一筆 AI 工作，逐頁讓 AI（Opus）讀、校對員（PaddleOCR）比對。

- 排入：上傳後自動開始；本案排隊中的頁數加這份超過上限時改成「等待確認」；
- 執行：每批 PROOF_BATCH 頁。先在背景啟動校對員，同時逐頁呼叫 AI、讀完立刻存；最後等校對結果
  （等待期間持續更新心跳），寫在該頁「目前」的文字版本上；
- 一頁失敗不擋整份；額度用完、沒登入時整份停下，已讀完的頁照常可以對照；
- 中斷（後端重啟、過期）後可以「繼續辨識」：只補做還沒讀、還沒校對的頁。
"""
from __future__ import annotations

import math
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.ai.provider import AIError, AIProvider
from app.ingest.extract import file_ext
from app.ingest.service import current_page_text
from app.models import AIJob, Case, Document, Page, PageText
from app.ocr.alerts import compute_alerts
from app.ocr.images import ensure_page_image
from app.ocr.proofreader import ProofreaderUnavailable, finish_proofreader, kill_proofreader, start_proofreader
from app.ocr.state import (
    AUTO_OCR_PAGE_LIMIT, OCR_AI_TIMEOUT_S, PROOF_BATCH, QUEUE_KEEPALIVE_S, estimate_minutes, needs_check,
)
from app.storage import LocalStorage

OCR_SYSTEM = ("你是文件辨識程式。逐字抄錄圖片中的文字，保持原本的行序與換行；表格每列一行，欄位以兩個空格分隔。"
              "勾選框照原樣抄錄（□、■、☑、☐ 等）。看不清楚的字，每個字寫一個「〔?〕」，不要猜、不要補字。"
              "不得修正錯字、計算錯誤或不合理的日期，原文寫什麼就抄什麼。印章內的文字放在最後，每行開頭標【印章】。"
              "圖片沒有任何文字時輸出空字串。只輸出抄錄結果。")
OCR_USER = "請抄錄這一頁。"
OCR_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
FATAL_KINDS = {"usage_limit", "not_logged_in"}  # 整份停下；其他錯誤只讓該頁失敗


def _now() -> datetime:
    return datetime.now(timezone.utc)


def confirm_message(pages: int) -> str:
    return f"這次要辨識 {pages} 頁掃描頁，預估約 {estimate_minutes(pages)} 分鐘，要開始辨識嗎？"


def pages_to_read(s: Session, document_id: UUID) -> list[Page]:
    return list(s.scalars(select(Page).where(Page.document_id == document_id, Page.ocr_state == "pending")
                          .order_by(Page.page_no)))


def pages_to_proof(s: Session, document_id: UUID) -> list[Page]:
    return list(s.scalars(select(Page).where(Page.document_id == document_id, Page.ocr_state == "read")
                          .order_by(Page.page_no)))


def work_pages(s: Session, document_id: UUID) -> int:
    return s.scalar(select(func.count()).select_from(Page).where(
        Page.document_id == document_id, Page.ocr_state.in_(("pending", "read")))) or 0


def latest_ocr_job(s: Session, document_id: UUID) -> AIJob | None:
    return s.scalars(select(AIJob).where(AIJob.document_id == document_id, AIJob.step == "ocr")
                     .order_by(AIJob.started_at.desc()).limit(1)).first()


def active_job(s: Session, document_id: UUID) -> AIJob | None:
    from app.pipeline.jobs import _stale
    job = latest_ocr_job(s, document_id)
    if job is not None and (job.status == "waiting" or job.status == "running" and not _stale(job)):
        return job
    return None


def queued_pages(s: Session, case_id: UUID) -> int:
    """本案正在辨識（running、沒過期）的文件還要處理的頁數；等待確認的文件不算。"""
    from app.pipeline.jobs import _stale
    total = 0
    for doc_id in s.scalars(select(Document.id).where(Document.case_id == case_id)):
        job = latest_ocr_job(s, doc_id)
        if job is not None and job.status == "running" and not _stale(job):
            total += work_pages(s, doc_id)
    return total


def queue_ocr(s: Session, document_id: UUID, member_id: UUID | None, *, confirm: bool, auto: bool) -> AIJob | None:
    """排入辨識。呼叫端只對 running 的工作排背景執行；waiting 要等律師確認。"""
    from app.pipeline.jobs import BUSY, TAKEN_OVER, JobBusy, JobRule, _stale, start_job
    doc = s.get(Document, document_id)
    s.get(Case, doc.case_id, with_for_update=True)  # 同時按兩次只會排一筆
    n = work_pages(s, document_id)
    if n == 0:
        return None
    last = latest_ocr_job(s, document_id)
    if last is not None and last.status == "running":
        if not _stale(last):
            raise JobBusy(BUSY)
        last.status, last.error, last.finished_at = "failed", TAKEN_OVER, _now()
    waiting = last if last is not None and last.status == "waiting" else None
    if waiting is not None and auto and not confirm:
        return waiting
    over = queued_pages(s, doc.case_id) + n > AUTO_OCR_PAGE_LIMIT
    if over and not confirm:
        if not auto:
            raise JobRule("confirm_ocr", confirm_message(n))
        return start_job(s, doc.case_id, None, "ocr", member_id, document_id=document_id, status="waiting")
    if waiting is not None:
        now = _now()
        waiting.status, waiting.started_at, waiting.heartbeat_at = "running", now, now
        if member_id is not None:
            waiting.member_id = member_id
        return waiting
    return start_job(s, doc.case_id, None, "ocr", member_id, document_id=document_id)


def retry_failed(s: Session, document_id: UUID) -> int:
    pages = list(s.scalars(select(Page).where(Page.document_id == document_id, Page.ocr_state == "failed")))
    for p in pages:
        p.ocr_state, p.ocr_error = "pending", None
    s.flush()
    return len(pages)


def ocr_progress(s: Session, document_id: UUID) -> dict:
    states = list(s.scalars(select(Page.ocr_state).where(Page.document_id == document_id, Page.ocr_state != "none")))
    total, done, failed, proofing = len(states), states.count("done"), states.count("failed"), states.count("read")
    remaining = total - done - failed
    if remaining <= 0:
        minutes = 0
    else:
        job = latest_ocr_job(s, document_id)
        completed = done + failed
        if completed and job is not None and job.status == "running":
            per_page = max((_now() - job.started_at).total_seconds(), 0) / completed
            minutes = max(1, math.ceil(per_page * remaining / 60))
        else:
            minutes = estimate_minutes(remaining)
    return {"total": total, "done": done, "failed": failed, "proofing": proofing, "minutes_left": minutes}


def current_texts(s: Session, page_ids: list[UUID]) -> dict[UUID, PageText]:
    """多頁的目前文字版本（一次查詢）。"""
    if not page_ids:
        return {}
    top = (select(PageText.page_id, func.max(PageText.version).label("v"))
           .where(PageText.page_id.in_(page_ids)).group_by(PageText.page_id).subquery())
    rows = s.scalars(select(PageText).join(top, (PageText.page_id == top.c.page_id) & (PageText.version == top.c.v)))
    return {pt.page_id: pt for pt in rows}


def document_ocr_json(s: Session, doc: Document, pages: list[Page]) -> dict:
    from app.pipeline.jobs import job_json
    active = active_job(s, doc.id)
    states = {p.ocr_state for p in pages}
    can_reocr = file_ext(doc.filename) == ".pdf" and "none" in states
    return {"job": job_json(latest_ocr_job(s, doc.id)), "progress": ocr_progress(s, doc.id),
            "can_reocr": can_reocr, "scan_hint": bool(can_reocr and doc.scan_hint),
            "can_resume": active is None and bool(states & {"pending", "read"})}


def page_ocr_fields(page: Page, pt: PageText | None) -> dict:
    return {"ocr_state": page.ocr_state, "needs_check": needs_check(pt),
            "alert_count": len(pt.alerts or []) if pt is not None else 0,
            "verified": bool(pt is not None and pt.needs_image_check and pt.verified_against_image)}


def pending_without_job(s: Session, case_id: UUID) -> dict[UUID, int]:
    """本案沒有進行中工作的文件各有幾頁待辨識（「開始辨識（N 頁）」用）。"""
    out = {}
    for doc_id in s.scalars(select(Document.id).where(Document.case_id == case_id).order_by(Document.created_at)):
        n = len(pages_to_read(s, doc_id))
        if n and active_job(s, doc_id) is None:
            out[doc_id] = n
    return out


# ---- 執行 ----

def _lock_page(s: Session, page_id: UUID) -> Page:
    """JobContext.write 已鎖案件列；這裡接著鎖頁列（上鎖順序：案件 → 頁）。"""
    return s.get(Page, page_id, with_for_update=True, populate_existing=True)


def _save_read(s: Session, page_id: UUID, text: str) -> None:
    page = _lock_page(s, page_id)
    if page.ocr_state != "pending":
        return  # 辨識期間已有人手動輸入或改了狀態：不覆蓋
    old = current_page_text(s, page_id)
    pt = PageText(firm_id=page.firm_id, page_id=page_id, version=(old.version + 1) if old else 1, source="ai_ocr",
                  text=text, needs_image_check=True, alerts=compute_alerts(text, None, True))
    s.add(pt)
    page.ocr_state, page.ocr_error = "read", None
    s.flush()
    if old is not None:  # 原本已有文字（例：改用辨識）：引用它的地方改指新版本並退回未審
        from app.ocr.service import after_text_change
        after_text_change(s, page, pt, None)


def _mark_failed(s: Session, page_id: UUID, error: str) -> None:
    page = _lock_page(s, page_id)
    if page.ocr_state == "pending":
        page.ocr_state, page.ocr_error = "failed", error[:1000]


def _save_proofs(s: Session, proofs: dict[UUID, list[dict] | None]) -> None:
    """校對結果寫在該頁「目前」的文字版本上（讀完後若已有人修正，比對的是修正後的文字）。"""
    for page_id, cells in proofs.items():
        page = _lock_page(s, page_id)
        if page.ocr_state != "read":
            continue
        page.proof = cells
        pt = current_page_text(s, page_id)
        if pt is not None:
            pt.alerts = compute_alerts(pt.text, cells, cells is not None)
        page.ocr_state = "done"


def _batch_images(firm_id: UUID, storage: LocalStorage, batch: list[tuple[UUID, int, str]], tmp: Path,
                  ctx) -> list[tuple[UUID, int, str, bytes, Path]]:
    """每頁原圖（第一次需要時產生）寫成暫存 PNG；產生不了原圖的頁標成失敗。"""
    from app.db import tenant_session
    out = []
    for page_id, page_no, state in batch:
        try:
            with tenant_session(firm_id) as s:
                key = ensure_page_image(s, storage, s.get(Page, page_id))
            png = storage.get(key)
        except Exception as e:  # 壞掉的 PDF 頁：只讓這一頁失敗
            ctx.write(lambda s, pid=page_id, err=f"other: 無法產生原圖（{type(e).__name__}）": _mark_failed(s, pid, err))
            continue
        path = tmp / f"{page_no}.png"
        path.write_bytes(png)
        out.append((page_id, page_no, state, png, path))
    return out


def _run_batch(job: AIJob, ctx, settings, storage: LocalStorage, batch: list[tuple[UUID, int, str]]) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ocr-"))
    proc = None
    read: list[UUID] = []
    try:
        items = _batch_images(ctx.firm_id, storage, batch, tmp, ctx)
        proc = start_proofreader(settings.paddle_cmd, [i[4] for i in items], tmp / "proof.json")
        for page_id, _, state, png, _ in items:
            if state == "read":
                read.append(page_id)
                continue
            try:
                result = ctx.call("ocr", OCR_SYSTEM, OCR_USER, OCR_SCHEMA, "low", images=[png],
                                  model=settings.ocr_model, timeout_s=OCR_AI_TIMEOUT_S)
            except AIError as e:
                if e.kind in FATAL_KINDS:
                    # 整份停下：本批已讀完的頁直接完成（沒有校對），律師照常可以對照
                    kill_proofreader(proc)
                    ctx.write(lambda s: _save_proofs(s, {pid: None for pid in read}))
                    raise
                ctx.write(lambda s, pid=page_id, err=f"{e.kind}: {e.message}": _mark_failed(s, pid, err))
                continue
            text = str(result.data.get("text") or "")
            ctx.write(lambda s, pid=page_id, t=text: _save_read(s, pid, t))
            read.append(page_id)
        index = {i[0]: n for n, i in enumerate(items)}
        if proc is None and settings.paddle_cmd and read:
            # 開始時校對員被別份文件占用：AI 讀完後等名額（期間更新心跳），不要直接略過校對
            proc = start_proofreader(settings.paddle_cmd, [i[4] for i in items], tmp / "proof.json",
                                     wait_s=settings.paddle_timeout_s, on_wait=ctx.beat)
        try:
            cells = finish_proofreader(proc, tmp / "proof.json", len(items), settings.paddle_timeout_s, ctx.beat)
        except ProofreaderUnavailable:
            cells = None
        proc = None  # finish_proofreader 已結束程序並釋放名額
        ctx.write(lambda s: _save_proofs(s, {pid: (cells[index[pid]] if cells is not None else None)
                                             for pid in read}))
        ctx.beat()
    finally:
        kill_proofreader(proc)
        shutil.rmtree(tmp, ignore_errors=True)


def _touch_waiting(firm_id: UUID, job_ids: list[UUID], *, start: bool = False) -> bool:
    """更新還在跑（running）的工作心跳；start＝輪到它了，開始時間也改成現在。回傳是否還有工作在跑。"""
    from app.db import tenant_session
    now = _now()
    with tenant_session(firm_id) as s:
        jobs = list(s.scalars(select(AIJob).where(AIJob.id.in_(job_ids), AIJob.status == "running")))
        for j in jobs:
            j.heartbeat_at = now
            if start:
                j.started_at = now
    return bool(jobs)


def run_ocr_queue(firm_id: UUID, job_ids: list[UUID], settings, provider_factory,
                  keepalive_s: float = QUEUE_KEEPALIVE_S) -> None:
    """「開始辨識（N 頁）」一次排了多份文件：一份接一份跑。排隊中的工作定期更新心跳，
    輪到時才記開始時間，才不會在排隊時被判定中斷（後端真的停掉時，心跳跟著停，照常可以「繼續辨識」）。"""
    import threading

    from app.pipeline.jobs import run_job
    waiting = list(job_ids)
    lock = threading.Lock()
    stop = threading.Event()

    def keepalive() -> None:
        while not stop.wait(keepalive_s):
            with lock:
                ids = list(waiting)
            if ids:
                _touch_waiting(firm_id, ids)

    t = threading.Thread(target=keepalive, daemon=True)
    t.start()
    try:
        for job_id in job_ids:
            with lock:
                waiting.remove(job_id)
            if _touch_waiting(firm_id, [job_id], start=True):  # 已被接手或取消的就略過
                run_job(firm_id, job_id, settings, provider_factory=provider_factory)
    finally:
        stop.set()
        t.join()


def run_ocr_document(job: AIJob, provider: AIProvider, ctx) -> None:
    from app.db import tenant_session
    settings = db.settings()
    storage = LocalStorage(settings.storage_dir)
    with tenant_session(ctx.firm_id) as s:
        pages = sorted(pages_to_read(s, job.document_id) + pages_to_proof(s, job.document_id), key=lambda p: p.page_no)
        work = [(p.id, p.page_no, p.ocr_state) for p in pages]
    for i in range(0, len(work), PROOF_BATCH):
        _run_batch(job, ctx, settings, storage, work[i:i + PROOF_BATCH])
