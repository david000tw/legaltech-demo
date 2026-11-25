"""計畫 2A 備案（設計 5.1、10.15）：只用 AI 辨識，不跑校對員（2026-11-25 使用者決定）。"""
from fastapi.testclient import TestClient

from app import db as dbmod
from app.config import Settings
from app.db import tenant_session
from app.main import create_app
from app.models import Page, PageText
from app.ocr import job as ocr_job
from app.ocr.state import UNREADABLE
from app.pipeline.jobs import run_job
from tests.conftest import make_settings
from tests.helpers import create_firm, create_member, login
from tests.test_ocr_job import FAKE_PADDLE, fake, kinds, make_doc, page_ids, queue, states, texts


def _ai_only(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, paddle_cmd=FAKE_PADDLE, ocr_proofread=False)  # 裝了校對員也不跑
    dbmod.configure(settings)

    def boom(*a, **kw):
        raise AssertionError("只用 AI 時不能啟動校對員")
    monkeypatch.setattr(ocr_job, "start_proofreader", boom)
    return settings


def test_default_is_ai_only():
    assert Settings(_env_file=None).ocr_proofread is False


def test_pages_done_without_proofreader_or_missing_alert(db, tmp_path, monkeypatch):
    _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 2)
    job_id, _ = queue(firm, doc)
    run_job(firm, job_id, dbmod.settings(), provider=fake("收據 合計  5,780元", f"醫療費用{UNREADABLE}元"))
    assert states(firm, doc) == ["done", "done"]
    # 沒有「校對員未執行」，也沒有校對不一致；規則檢查照做（看不清楚的字仍標紅）
    assert [kinds(pt) for pt in texts(firm, doc)] == [[], ["unreadable"]]


def test_read_page_is_finished_without_ai_or_proofreader(db, tmp_path, monkeypatch):
    # 校對模式時中斷、停在「校對中」（read）的頁：改成只用 AI 後接手，直接完成，不重做 AI
    _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 2)
    p1, _ = page_ids(firm, doc)
    with tenant_session(firm) as s:
        s.add(PageText(firm_id=firm, page_id=p1, version=1, source="ai_ocr", text="第一頁", needs_image_check=True))
        s.get(Page, p1).ocr_state = "read"
    job_id, _ = queue(firm, doc)
    provider = fake("第二頁")
    run_job(firm, job_id, dbmod.settings(), provider=provider)
    assert len(provider.calls) == 1
    assert states(firm, doc) == ["done", "done"]
    assert kinds(texts(firm, doc)[0]) == []


def test_manual_correction_has_no_missing_proofreader_alert(db, tmp_path, monkeypatch):
    settings = _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    create_member(firm, "lawyer@a.test")
    client = TestClient(create_app(settings))
    assert login(client, "lawyer@a.test").status_code == 200
    _, doc = make_doc(firm, 1)
    job_id, _ = queue(firm, doc)
    run_job(firm, job_id, settings, provider=fake("醫療費用合計新臺幣5,780元"))
    (pid,) = page_ids(firm, doc)
    r = client.put(f"/api/pages/{pid}/text", json={"text": "醫療費用合計新臺幣5,870元", "base_version": 1,
                                                   "verify": False})
    assert r.status_code == 200, r.text
    assert kinds(texts(firm, doc)[0]) == []


def test_page_is_done_as_soon_as_it_is_read(db, tmp_path, monkeypatch):
    # 同一批後面的頁還在辨識時，讀完的頁已經完成：不顯示「校對中」，律師可以勾「已對照原圖」
    from app.ocr.service import verify_page
    from tests.fakes.fake_provider import FakeProvider
    from tests.test_ocr_correction import member
    _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    lawyer = member(firm, "lawyer@a.test")
    _, doc = make_doc(firm, 2)
    p1, _ = page_ids(firm, doc)
    seen: list = []

    def handler(call):
        if seen:  # 第二頁辨識中
            with tenant_session(firm) as s:
                seen.append(s.get(Page, p1).ocr_state)
                verify_page(s, p1, 1, lawyer)
        seen.append("call")
        return {"text": "第一頁" if len(seen) == 1 else "第二頁"}
    job_id, _ = queue(firm, doc)
    run_job(firm, job_id, dbmod.settings(), provider=FakeProvider({"ocr": handler}))
    assert seen == ["call", "done", "call"]


def test_usage_limit_stop_finishes_read_pages(db, tmp_path, monkeypatch):
    from app.ai.provider import AIError
    from app.models import AIJob
    _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    _, doc = make_doc(firm, 3)
    job_id, _ = queue(firm, doc)
    run_job(firm, job_id, dbmod.settings(), provider=fake("第一頁", AIError("usage_limit", "額度用完")))
    assert states(firm, doc) == ["done", "pending", "pending"]
    assert kinds(texts(firm, doc)[0]) == []
    with tenant_session(firm) as s:
        assert s.get(AIJob, job_id).status == "failed"


def test_correction_ignores_old_proofreader_result(db, tmp_path, monkeypatch):
    # 改成只用 AI 之前已校對過的頁：修正文字後不再拿舊的校對結果比對，不出現「校對不一致」
    from tests.test_ocr_correction import cell
    settings = _ai_only(tmp_path, monkeypatch)
    firm = create_firm("甲事務所")
    create_member(firm, "lawyer@a.test")
    client = TestClient(create_app(settings))
    assert login(client, "lawyer@a.test").status_code == 200
    _, doc = make_doc(firm, 1)
    job_id, _ = queue(firm, doc)
    run_job(firm, job_id, settings, provider=fake("醫療費用合計新臺幣5,780元"))
    (pid,) = page_ids(firm, doc)
    with tenant_session(firm) as s:
        s.get(Page, pid).proof = [cell("完全不同的一行字")]
    r = client.put(f"/api/pages/{pid}/text", json={"text": "醫療費用合計新臺幣5,870元", "base_version": 1,
                                                   "verify": False})
    assert r.status_code == 200, r.text
    assert kinds(texts(firm, doc)[0]) == []
