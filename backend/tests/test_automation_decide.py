"""自動流程的判斷規則（設計 6.2）：只讀資料庫，回傳該開始的動作或等待原因。判斷表每一列一個測試。"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.automation.decide import Wait, automation_view, evidence_wait, next_case_action, next_round_action
from app.automation.fingerprints import notes_input_fingerprint
from app.db import tenant_session
from app.models import AIJob, Case, Claim, Document, Exhibit, Page, Round
from app.pipeline.jobs import JOB_STALE_AFTER
from app.pipeline.run import STALE_AFTER
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

pytestmark = pytest.mark.automation_real

NOW = datetime.now(timezone.utc)


@pytest.fixture
def firm(db):
    return create_firm("甲事務所")


def _round(firm, *, evidence=None, **fields):
    case_id, rid = make_round(firm, our_side="defendant", evidence=evidence)
    _set(firm, rid, **fields)
    return case_id, rid


def _set(firm, rid, **fields):
    with tenant_session(firm) as s:
        rnd = s.get(Round, rid)
        for k, v in fields.items():
            setattr(rnd, k, v)


def _action(firm, rid):
    with tenant_session(firm) as s:
        return next_round_action(s, rid)


def _scan_doc(firm, case_id, state, *, kind="evidence", round_id=None, job_status=None, heartbeat=NOW):
    """一份還沒有文字的掃描文件（一頁），可附一筆辨識工作。"""
    with tenant_session(firm) as s:
        doc = Document(firm_id=firm, case_id=case_id, round_id=round_id, kind=kind, filename="掃描.pdf",
                       storage_key=f"k/{state}", sha256=hashlib.sha256(state.encode()).hexdigest(), page_count=1)
        s.add(doc)
        s.flush()
        if kind == "evidence":
            s.add(Exhibit(firm_id=firm, case_id=case_id, document_id=doc.id, prefix="被證", number=9))
        s.add(Page(firm_id=firm, document_id=doc.id, page_no=1, needs_ocr=True, ocr_state=state))
        if job_status:
            s.add(AIJob(firm_id=firm, case_id=case_id, document_id=doc.id, step="ocr", status=job_status,
                        started_at=heartbeat, heartbeat_at=heartbeat))
        return doc.id


def _untick(firm, rid):
    with tenant_session(firm) as s:
        s.add(Claim(firm_id=firm, round_id=rid, seq=1, summary="被告未讓路", quote="", selected=False))


# ---- 判斷表 ----

def test_manual_mode_does_nothing(firm):
    case_id, rid = _round(firm, auto_next="extract")
    with tenant_session(firm) as s:
        s.get(Case, case_id).automation = "manual"
    assert _action(firm, rid) is None


def test_unarmed_round_does_nothing(firm):
    _, rid = _round(firm, status="extracted")
    assert _action(firm, rid) is None


def test_unarmed_round_with_rule_note_shows_blocked(firm):
    _, rid = _round(firm, auto_note="沒有勾選任何主張")
    assert _action(firm, rid) == Wait("blocked", "沒有勾選任何主張")


def test_armed_extract_without_opponent_brief_does_nothing(firm):
    case_id, rid = make_round(firm, our_side="defendant", opponent_pages=())
    _set(firm, rid, auto_next="extract")
    assert _action(firm, rid) is None


def test_armed_extract_waits_for_opponent_ocr(firm):
    case_id, rid = _round(firm, auto_next="extract")
    _scan_doc(firm, case_id, "failed", kind="opponent_brief", round_id=rid)
    w = _action(firm, rid)
    assert isinstance(w, Wait) and w.code == "opponent_ocr" and "辨識" in w.message


def test_armed_extract_starts(firm):
    _, rid = _round(firm, auto_next="extract")
    assert _action(firm, rid) == "extract"


def test_new_brief_after_extraction_reextracts(firm):
    _, rid = _round(firm, auto_next="extract", status="extracted", extract_fingerprint="old")
    assert _action(firm, rid) == "reextract"


def test_new_brief_after_lawyer_unticked_waits(firm):
    _, rid = _round(firm, auto_next="extract", status="extracted", extract_fingerprint="old")
    _untick(firm, rid)
    assert _action(firm, rid) == Wait("new_brief", "有新上傳的書狀")


def test_null_extract_fingerprint_never_reextracts(firm):
    _, rid = _round(firm, auto_next="draft", status="extracted", extract_fingerprint=None)
    assert _action(firm, rid) == "draft"


def test_stale_extract_resumes_once(firm):
    _, rid = _round(firm, auto_next="extract", status="extracting", started_at=NOW - STALE_AFTER - timedelta(minutes=1))
    assert _action(firm, rid) == "resume_extract"
    _set(firm, rid, auto_retries=1)
    assert _action(firm, rid) is None


def test_stale_draft_resumes_once(firm):
    _, rid = _round(firm, auto_next="draft", status="drafting", started_at=NOW - STALE_AFTER - timedelta(minutes=1))
    assert _action(firm, rid) == "resume_draft"


def test_running_stage_is_left_alone(firm):
    _, rid = _round(firm, auto_next="draft", status="drafting", started_at=NOW)
    assert _action(firm, rid) is None


def test_draft_with_new_brief_reextracts_first(firm):
    _, rid = _round(firm, auto_next="draft", status="extracted", extract_fingerprint="old")
    assert _action(firm, rid) == "reextract"


def test_draft_waits_for_evidence_ocr(firm):
    case_id, rid = _round(firm, auto_next="draft", status="extracted")
    _scan_doc(firm, case_id, "pending", job_status="running")
    w = _action(firm, rid)
    assert isinstance(w, Wait) and w.code == "evidence_ocr"


def test_draft_waits_for_running_notes(firm):
    case_id, rid = _round(firm, auto_next="draft", status="extracted")
    with tenant_session(firm) as s:
        s.add(AIJob(firm_id=firm, case_id=case_id, step="evidence_notes", status="running", started_at=NOW,
                    heartbeat_at=NOW))
    assert _action(firm, rid) == Wait("evidence_notes_running", "等卷證說明完成")


def test_draft_waits_for_notes_that_are_due(firm):
    """該跑還沒人啟動：hint start_notes，kick_round 會先去啟動卷證說明（審查 #1）。"""
    _, rid = _round(firm, auto_next="draft", status="extracted", evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    assert _action(firm, rid) == Wait("evidence_notes_running", "等卷證說明完成", hint="start_notes")


def test_draft_starts_when_evidence_ready(firm):
    case_id, rid = _round(firm, auto_next="draft", status="extracted", evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    with tenant_session(firm) as s:  # 卷證說明已經自動試過這份輸入
        s.get(Case, case_id).notes_auto_fingerprint = notes_input_fingerprint(s, case_id)
    assert _action(firm, rid) == "draft"


def test_failed_round_does_nothing(firm):
    _, rid = _round(firm, auto_next="draft", status="failed", failed_stage="draft")
    assert _action(firm, rid) is None


def test_complaint_draft_waits_for_evidence(firm):
    case_id, rid = _round(firm, auto_next="draft", status="extracted", brief_kind="complaint")
    _scan_doc(firm, case_id, "pending")
    w = _action(firm, rid)
    assert isinstance(w, Wait) and w.code == "evidence_ocr"


# ---- 證據側 ----

def _case_action(firm, case_id):
    with tenant_session(firm) as s:
        return next_case_action(s, case_id)


def test_notes_start_when_due(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    assert _case_action(firm, case_id) == "evidence_notes"


def test_notes_not_in_manual_mode(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    with tenant_session(firm) as s:
        s.get(Case, case_id).automation = "manual"
    assert _case_action(firm, case_id) is None


def test_notes_wait_for_evidence_ocr(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    _scan_doc(firm, case_id, "pending", job_status="running")
    assert _case_action(firm, case_id) is None


def test_notes_nothing_pending(firm):
    case_id, _ = _round(firm)
    assert _case_action(firm, case_id) is None


def test_notes_same_input_not_retried(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    with tenant_session(firm) as s:
        s.get(Case, case_id).notes_auto_fingerprint = notes_input_fingerprint(s, case_id)
    assert _case_action(firm, case_id) is None


def test_notes_not_while_running(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    with tenant_session(firm) as s:
        s.add(AIJob(firm_id=firm, case_id=case_id, step="evidence_notes", status="running", started_at=NOW,
                    heartbeat_at=NOW))
    assert _case_action(firm, case_id) is None


def test_stale_notes_job_counts_as_not_running(firm):
    case_id, _ = _round(firm, evidence={"01_研判表.txt": "研判結果：被告未讓路。"})
    old = NOW - JOB_STALE_AFTER - timedelta(minutes=1)
    with tenant_session(firm) as s:
        s.add(AIJob(firm_id=firm, case_id=case_id, step="evidence_notes", status="running", started_at=old,
                    heartbeat_at=old))
    assert _case_action(firm, case_id) == "evidence_notes"


# ---- 等證據的提示 ----

@pytest.mark.parametrize("state,job,hint", [
    ("failed", None, "retry_failed"),
    ("pending", "waiting", "confirm_ocr"),
    ("pending", None, "resume_ocr"),
    ("pending", "running", None),
])
def test_evidence_wait_hints(firm, state, job, hint):
    case_id, _ = _round(firm)
    doc = _scan_doc(firm, case_id, state, job_status=job)
    with tenant_session(firm) as s:
        w = evidence_wait(s, case_id)
    assert w.code == "evidence_ocr" and w.hint == hint and w.document_id == doc
    assert "被證9" in w.message


def test_stale_ocr_job_hint_is_resume(firm):
    case_id, _ = _round(firm)
    _scan_doc(firm, case_id, "pending", job_status="running", heartbeat=NOW - timedelta(hours=1))
    with tenant_session(firm) as s:
        assert evidence_wait(s, case_id).hint == "resume_ocr"


def test_automation_view(firm):
    case_id, rid = _round(firm, auto_next="draft", status="extracted")
    _scan_doc(firm, case_id, "pending", job_status="running")
    with tenant_session(firm) as s:
        v = automation_view(s, rid)
    assert v["mode"] == "auto" and v["auto_next"] == "draft" and v["running"] is None
    assert v["wait"]["code"] == "evidence_ocr"
