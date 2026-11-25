"""計畫 2B：何時排入支持度檢查，以及放行與重新檢查的介面（設計 7.2、7.3）。"""
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app import db as dbmod
from app.ai.provider import AIError
from app.db import admin_session, tenant_session
from app.models import Citation, PageText, Paragraph, SupportCheck, UsageRecord
from app.support import fingerprint as fp
from app.support import job as support_job
from tests.export_helpers import clients
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round, run_two_stage
from tests.verify_helpers import E_QUOTE, EVIDENCE_TEXT, J_REASONING, make_paragraph, setup_verify_round

pytestmark = pytest.mark.support_real

BAR = "(110)北律字第0001號"
TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
_BLOCK = re.compile(r"【引用 (C\d+)】(.*?)(?=【引用 C\d+】|\Z)", re.S)


def _support_fake(rule):
    """rule(引文) -> verdict；只看【引文】那一行，句子裡展開的其他引文不算。"""
    def handler(call):
        items = []
        for code, block in _BLOCK.findall(call["user"]):
            quote = block.split("【引文】", 1)[1].split("\n", 1)[0]
            items.append({"id": code, "verdict": rule(quote), "explanation": "原文沒有提到闖紅燈"})
        return {"items": items}
    return handler


def _j1_bad(quote):
    return "not_supported" if quote == J_REASONING else "supports"


def _all_supports(quote):
    return "supports"


def _setup(db, provider=None):
    ctx = setup_verify_round()
    c = clients(ctx, db)
    pid = make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)])
    _use(c, provider or FakeProvider({"support": _support_fake(_all_supports)}))
    return ctx, c, pid


def _use(c, provider):
    for client in c.values():
        client.app.state.provider_factory = lambda settings, firm: provider


def _draft(c, ctx, who="lawyer"):
    r = c[who].get(f"/api/drafts/{ctx['draft_id']}")
    assert r.status_code == 200, r.text
    return r.json()


def _cite(body, code, n=0):
    return next(x for x in body["paragraphs"][n]["citations"] if x["code"] == code)


def _reasons(body):
    return {b["reason"] for b in body["blockers"]}


def _rows(ctx):
    with tenant_session(ctx["firm"]) as s:
        return [(r.status, r.verdict) for r in s.scalars(select(SupportCheck))]


def _all_done(ctx, pid):
    """這一段每個引用都有已完成的列。"""
    with tenant_session(ctx["firm"]) as s:
        for inp in fp.support_inputs(s, s.get(Paragraph, pid)).values():
            row = s.scalars(select(SupportCheck).where(SupportCheck.fingerprint == inp.fingerprint)).one()
            assert row.status == "done", (inp.code, row.status, row.error)


def _review(c, ctx, pid):
    lawyer = c["lawyer"]
    with tenant_session(ctx["firm"]) as s:
        j1 = s.scalars(select(Citation.id).where(Citation.paragraph_id == pid, Citation.code == "J1")).one()
    assert lawyer.post(f"/api/citations/{j1}/confirm").status_code == 200
    v = next(p["version"] for p in _draft(c, ctx)["paragraphs"] if p["id"] == str(pid))
    return lawyer.post(f"/api/paragraphs/{pid}/review", json={"version": v})


def test_open_draft_queues_and_shows_results(db):
    ctx, c, _ = _setup(db, FakeProvider({"support": _support_fake(_j1_bad)}))
    first = _draft(c, ctx)  # 回應時還在檢查中；TestClient 回應後同步執行背景工作
    assert _cite(first, "J1")["support"]["status"] == "pending"
    body = _draft(c, ctx)
    j1, e1 = _cite(body, "J1")["support"], _cite(body, "E1")["support"]
    assert (j1["verdict"], j1["blocking"], j1["explanation"]) == ("not_supported", True, "原文沒有提到闖紅燈")
    assert (e1["verdict"], e1["blocking"]) == ("supports", False)
    assert "support_not_supported:J1" in _reasons(body) and "support_pending:E1" not in _reasons(body)


def test_release_route_and_review(db):
    ctx, c, pid = _setup(db, FakeProvider({"support": _support_fake(_j1_bad)}))
    _draft(c, ctx)
    cite = _cite(_draft(c, ctx), "J1")
    j1, seen = cite["id"], cite["support"]["version"]
    url = f"/api/citations/{j1}/support/release"
    assert c["assistant"].post(url, json={"reason": "x", "version": seen}).status_code == 403
    assert c["lawyer"].post(url, json={"reason": "x"}).status_code == 422  # 一定要帶律師看到的那一版
    r = c["lawyer"].post(url, json={"reason": "x", "version": "舊的一版"})
    assert r.status_code == 409 and r.json()["detail"] == "支持度檢查結果已更新，請重新載入後再確認"
    r = c["lawyer"].post(url, json={"reason": " ", "version": seen})
    assert r.status_code == 409 and r.json()["detail"] == "請填寫放行理由"
    r = c["lawyer"].post(url, json={"reason": "原文第二段另有說明", "version": seen})
    assert r.status_code == 200, r.text
    released = next(x for x in r.json()["citations"] if x["code"] == "J1")["support"]["released"]
    assert (released["reason"], released["bar_number"], released["name"]) == ("原文第二段另有說明", BAR, "lawyer")
    assert _review(c, ctx, pid).status_code == 200


def test_edit_rechecks_only_that_paragraph(db):
    fake = FakeProvider({"support": _support_fake(_all_supports)})
    ctx, c, pid = _setup(db, fake)
    make_paragraph(ctx, "另查被告{{quote:E1}}，此有{{cite:E1}}足證。", [("E1", E_QUOTE)], seq=2)
    _draft(c, ctx)
    before = len(fake.calls)
    r = c["assistant"].patch(f"/api/paragraphs/{pid}", json={
        "text": "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可證。",
        "citations": [{"code": "J1", "quote": J_REASONING}, {"code": "E1", "quote": E_QUOTE}]})
    assert r.status_code == 200, r.text
    assert {x["support"]["status"] for x in r.json()["citations"]} == {"pending"}
    new = fake.calls[before:]
    assert len(new) == 1 and "可證" in new[0]["user"] and "足證" not in new[0]["user"]
    assert len(_BLOCK.findall(new[0]["user"])) == 2
    _all_done(ctx, pid)


def test_export_waits_while_pending(db, monkeypatch):
    ctx, c, pid = _setup(db)
    real = support_job.run_support_checks
    monkeypatch.setattr(support_job, "run_support_checks", lambda *a, **k: None)
    r = c["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409
    assert {"support_pending:J1", "support_pending:E1"} <= {b["reason"] for b in r.json()["blockers"]}
    monkeypatch.setattr(support_job, "run_support_checks", real)
    with tenant_session(ctx["firm"]) as s:  # 沒人接手超過 2 分鐘 → 打開草稿時重新排入
        s.execute(update(SupportCheck).values(requested_at=datetime.now(timezone.utc) - timedelta(minutes=3)))
    _draft(c, ctx)
    assert set(_rows(ctx)) == {("done", "supports")}
    assert _review(c, ctx, pid).status_code == 200
    assert c["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200


def _rewrite_fake():
    new = {"text": "被告未讓幹道車先行，此有{{cite:E1}}可稽。", "citations": [{"code": "E1", "quote": E_QUOTE}]}
    return FakeProvider({"rewrite": new, "support": _support_fake(_all_supports)})


@pytest.mark.parametrize("entry", ["filing-package", "rewrite", "insert-pin", "review"])
def test_entry_points_queue_checks(db, entry):
    ctx, c, pid = _setup(db, _rewrite_fake())
    lawyer = c["lawyer"]
    assert _rows(ctx) == []  # 從沒打開過草稿
    if entry == "filing-package":
        r = lawyer.post(f"/api/drafts/{ctx['draft_id']}/filing-package")
        assert r.status_code == 409 and "support_pending:J1" in {b["reason"] for b in r.json()["blockers"]}
    elif entry == "rewrite":
        assert lawyer.post(f"/api/paragraphs/{pid}/rewrite", json={"instruction": "精簡"}).status_code == 200
    elif entry == "insert-pin":
        from tests.test_insert_pin import _other_reasoning, _pin
        passage, q = _other_reasoning(ctx)
        pin = _pin(ctx["firm"], ctx["case_id"], passage)
        r = lawyer.post(f"/api/paragraphs/{pid}/insert-pin", json={"pin_id": pin, "quote": q})
        assert r.status_code == 200, r.text
    else:
        r = lawyer.post(f"/api/paragraphs/{pid}/review", json={"version": "x" * 16})
        assert r.status_code == 409
    _all_done(ctx, pid)


def test_review_after_source_page_changed_rechecks(db):
    ctx, c, pid = _setup(db)
    _draft(c, ctx)
    _draft(c, ctx)
    with admin_session() as s:  # 被引用頁的文字改了（例如 OCR 修正）：E1 的指紋改變，沒有人打開草稿
        pt = s.get(PageText, s.scalars(select(Citation.page_text_id).where(Citation.paragraph_id == pid,
                                                                             Citation.code == "E1")).one())
        pt.text = EVIDENCE_TEXT + "另載明路口無號誌。"
    with tenant_session(ctx["firm"]) as s:
        j1 = s.scalars(select(Citation.id).where(Citation.paragraph_id == pid, Citation.code == "J1")).one()
        v_text = s.get(Paragraph, pid).text
    assert c["lawyer"].post(f"/api/citations/{j1}/confirm").status_code == 200
    v = next(p["version"] for p in c["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["paragraphs"]
             if p["text"] == v_text)
    with tenant_session(ctx["firm"]) as s:  # 把上一步 GET 排入的新列刪掉，模擬「沒打開草稿就直接標記已審」
        e1 = fp.support_inputs(s, s.get(Paragraph, pid))["E1"].fingerprint
        s.execute(SupportCheck.__table__.delete().where(SupportCheck.fingerprint == e1))
    r = c["lawyer"].post(f"/api/paragraphs/{pid}/review", json={"version": v})
    assert r.status_code == 409 and "support_pending:E1" in r.json()["detail"]["reasons"]
    _all_done(ctx, pid)


def test_failed_check_retry_and_release(db):
    ctx, c, pid = _setup(db, FakeProvider({"support": AIError("usage_limit", "額度用完")}))
    _draft(c, ctx)
    body = _draft(c, ctx)
    assert _cite(body, "J1")["support"]["status"] == "failed"
    assert {"support_unchecked:J1", "support_unchecked:E1"} <= _reasons(body)
    e1 = _cite(body, "E1")["id"]
    assert c["lawyer"].post(f"/api/citations/{e1}/support/release", json={
        "reason": "AI 無法使用，已人工核對", "version": _cite(body, "E1")["support"]["version"]}).status_code == 200
    _use(c, FakeProvider({"support": _support_fake(_all_supports)}))
    r = c["assistant"].post(f"/api/drafts/{ctx['draft_id']}/support/retry")
    assert r.status_code == 200 and r.json() == {"queued": 1}
    body = _draft(c, ctx)
    assert _cite(body, "J1")["support"]["verdict"] == "supports"
    e1s = _cite(body, "E1")["support"]
    assert e1s["status"] == "failed" and e1s["released"]["reason"] == "AI 無法使用，已人工核對"
    assert c["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/support/retry").status_code == 409


def test_patch_and_export_concurrently(db):
    ctx, c, pid = _setup(db)
    barrier, codes, errors = threading.Barrier(2), {}, []

    def patch():
        try:
            barrier.wait()
            codes["patch"] = c["assistant"].patch(f"/api/paragraphs/{pid}", json={
                "text": TEXT + "被告應負全責。",
                "citations": [{"code": "J1", "quote": J_REASONING}, {"code": "E1", "quote": E_QUOTE}]}).status_code
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def export():
        try:
            barrier.wait()
            codes["export"] = c["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=patch), threading.Thread(target=export)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and codes["patch"] == 200 and codes["export"] == 409


def test_draft_completion_runs_checks(db):
    from tests.test_drafting import CLAIMS_OK, DRAFT_OK, SELECT_OK
    firm = create_firm("甲事務所")
    _, round_id = make_round(firm, evidence={"01_研判表.txt": EVIDENCE_TEXT})
    fake = FakeProvider({"claims": CLAIMS_OK, "select": SELECT_OK, "draft": DRAFT_OK,
                         "support": _support_fake(_all_supports)})
    run_two_stage(firm, round_id, dbmod.settings(), provider=fake)
    with tenant_session(firm) as s:
        rows = list(s.scalars(select(SupportCheck)))
        assert rows and all(r.status == "done" and r.verdict == "supports" for r in rows)
        assert s.scalars(select(UsageRecord).where(UsageRecord.step == "support")).first() is not None
