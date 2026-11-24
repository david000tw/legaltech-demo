"""子專案乙 Task 5：書狀健檢與爭點整理表（設計 5.8）。
（tests/test_health.py 是伺服器健康檢查，與這裡無關。）"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import tenant_session
from app.main import create_app
from app.models import AIJob, Claim, HealthReport, Round
from app.pipeline import health
from app.pipeline.health import health_schema, report_view
from app.pipeline.jobs import run_job, start_job
from tests.export_helpers import clients
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round
from tests.test_ingest import blank_pdf, upload
from tests.verify_helpers import E_QUOTE, setup_verify_round

OPP_QUOTE = "原告行經無號誌路口時亦未減速慢行"
HIGH = {"level": "high", "title": "與有過失可能成立", "explanation": "對造主張原告未減速，研判表亦有相關記載。",
        "claim_seqs": [1], "absence": False,
        "evidence": [{"source": "原證1", "page_no": 1, "quote": E_QUOTE},
                     {"source": "原證1", "page_no": 1, "quote": "這句話不在研判表裡"}],
        "references": ["J1", "S1"], "other_references": []}
GAP = {"level": "gap", "title": "缺少行車紀錄器影像", "explanation": "卷證沒有事故當時的影像。", "claim_seqs": [1],
       "absence": True, "evidence": [], "references": [], "other_references": []}


def report_data(items=(HIGH, GAP), undisputed=(), issues=("原告是否與有過失",)):
    return {"items": list(items), "undisputed": list(undisputed), "issues": list(issues)}


def drafted(kind="preparatory"):
    ctx = setup_verify_round()
    with tenant_session(ctx["firm"]) as s:
        rnd = s.get(Round, ctx["round_id"])
        rnd.status, rnd.brief_kind = "drafted", kind
        if kind == "complaint":
            member = create_member(ctx["firm"], "confirmer@a.test")
            s.add(Claim(firm_id=ctx["firm"], round_id=rnd.id, seq=1, kind="request", summary="醫療費用", quote="",
                        request_type="monetary", amount=45000, confirmed_by=member,
                        confirmed_at=datetime.now(timezone.utc)))
        else:
            s.add(Claim(firm_id=ctx["firm"], round_id=rnd.id, seq=1, summary="原告與有過失", quote=OPP_QUOTE,
                        quote_verified=True))
    return ctx


def run(ctx, data, settings, status="done"):
    fake = FakeProvider({"health": data})
    with tenant_session(ctx["firm"]) as s:
        job_id = start_job(s, ctx["case_id"], ctx["round_id"], "health", None).id
    run_job(ctx["firm"], job_id, settings, provider=fake)
    with tenant_session(ctx["firm"]) as s:
        job = s.get(AIJob, job_id)
        assert job.status == status, job.error
        return report_view(s, ctx["round_id"]), fake, job


def basis(b):
    return b["status"], b["reasons"]


def test_health_items_and_sources(db):
    ctx = drafted()
    report, fake, _ = run(ctx, report_data(), db)
    high, gap = report["items"]
    assert (high["level"], high["title"], high["claim_seqs"], high["absence"]) == ("high", "與有過失可能成立", [1], False)
    assert [basis(b) for b in high["basis"]] == [("found", []), ("not_found", ["quote_not_found"])]
    assert high["basis"][0]["label"] == "原證1 第1頁"
    refs = high["references"]
    assert [r["code"] for r in refs] == ["J1", "S1"] and "3946" in refs[0]["label"]
    assert refs[1]["label"] == "民法第191條之2"
    assert gap["absence"] is True and gap["basis"] == [] and high["not_in_list"] == []
    schema = fake.calls[0]["schema"]
    item = schema["properties"]["items"]["items"]["properties"]
    assert item["references"]["items"]["enum"] == ["J1", "J2", "S1"] and "undisputed" in schema["properties"]
    user = fake.calls[0]["user"]
    assert "【原證1 第1頁】" in user and "【對造書狀A 第1頁】" in user and "[J1]" in user and "主張1：原告與有過失" in user
    assert report["outdated"] is False and report["issues"] == ["原告是否與有過失"]
    assert report["issues_label"] == "本件爭點" and report["pending_ocr"] == []


def test_health_codes_in_text_show_names(db):
    """子專案乙驗收：140 項健檢有 84 項的說明寫「見J5」這類內部代碼，律師看不懂。
    讀取時把本輪候選代碼換成判決字號或法條名稱；不在本輪候選的代碼（J9、J10）原樣保留，J10 不會被當成 J1。"""
    ctx = drafted()
    item = {**HIGH, "title": "與有過失（S1）", "explanation": "類似情形見J1；J1、S1可參；J9 與 J10 不在清單。"}
    undisputed = [{"fact": "被告承認未減速（S1）", "source": "對造書狀A", "page_no": 1, "quote": OPP_QUOTE}]
    report, _, _ = run(ctx, report_data(items=[item], undisputed=undisputed, issues=["是否適用J1的見解"]), db)
    got = report["items"][0]
    j1 = got["references"][0]["label"]
    assert "3946" in j1
    assert got["title"] == "與有過失（民法第191條之2）"
    assert got["explanation"] == f"類似情形見{j1}；{j1}、民法第191條之2可參；J9 與 J10 不在清單。"
    assert report["undisputed"][0]["fact"] == "被告承認未減速（民法第191條之2）"
    assert report["issues"] == [f"是否適用{j1}的見解"]
    with tenant_session(ctx["firm"]) as s:  # 存的仍是代碼：判決法條名稱讀取時才算
        stored = s.scalars(select(HealthReport).where(HealthReport.round_id == ctx["round_id"])).one()
        assert stored.items[0]["explanation"] == item["explanation"]


def test_health_basis_can_cite_opponent(db):
    ctx = drafted()
    item = {**HIGH, "evidence": [{"source": "對造書狀A", "page_no": 1, "quote": OPP_QUOTE}]}
    report, _, _ = run(ctx, report_data(items=[item]), db)
    b = report["items"][0]["basis"][0]
    assert basis(b) == ("found", []) and b["label"] == "對造書狀「答辯狀.txt」第1頁"


def test_health_not_in_list(db):
    ctx = drafted()
    item = {**HIGH, "references": ["J1", "J9"], "other_references": ["民法第191條之2", "最高法院100年度台上字第1號"]}
    report, _, _ = run(ctx, report_data(items=[item]), db)
    got = report["items"][0]
    assert [r["code"] for r in got["references"]] == ["J1", "S1"]
    assert got["not_in_list"] == ["J9", "最高法院100年度台上字第1號"]


def test_health_undisputed_from_opponent(db):
    ctx = drafted()
    undisputed = [{"fact": "被告承認原告未減速", "source": "對造書狀A", "page_no": 1, "quote": OPP_QUOTE},
                  {"fact": "研判表記載被告未讓幹道車", "source": "原證1", "page_no": 1, "quote": E_QUOTE}]
    report, _, _ = run(ctx, report_data(undisputed=undisputed), db)
    got = report["undisputed"]
    assert [u["fact"] for u in got] == ["被告承認原告未減速", "研判表記載被告未讓幹道車"]
    assert [basis(u["basis"]) for u in got] == [("found", []), ("not_found", ["not_opponent"])]


def test_complaint_has_no_undisputed(db):
    ctx = drafted(kind="complaint")
    data = report_data(undisputed=[{"fact": "x", "source": "對造書狀A", "page_no": 1, "quote": OPP_QUOTE}])
    report, fake, _ = run(ctx, data, db)
    assert report["undisputed"] is None and report["issues_label"] == "預期爭點"
    assert "undisputed" not in fake.calls[0]["schema"]["properties"]
    user = fake.calls[0]["user"]
    assert "請求項目1：醫療費用（新臺幣45,000元）" in user and "對造書狀A" not in user


def test_health_schema_without_candidates():
    schema = health_schema([], with_undisputed=False)
    item = schema["properties"]["items"]["items"]["properties"]
    assert "references" not in item and "other_references" in item and "undisputed" not in schema["properties"]
    assert item["level"]["enum"] == ["high", "suggest", "gap"]


def test_health_too_long_fails_without_calling_ai(db, monkeypatch):
    monkeypatch.setattr(health, "SELECT_TOKEN_LIMIT", 10)
    ctx = drafted()
    report, fake, job = run(ctx, report_data(), db, status="failed")
    assert job.error == "卷證太多，超過一次健檢能處理的長度" and fake.calls == [] and report is None


def test_health_requires_drafted(lawyer_client):
    client, firm, _ = lawyer_client
    _, rid = make_round(firm)
    with tenant_session(firm) as s:
        s.get(Round, rid).status = "extracted"
    r = client.post(f"/api/rounds/{rid}/health")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "not_drafted", "message": "起草完成後才能執行健檢"}
    with tenant_session(firm) as s:
        assert s.scalar(select(func.count()).select_from(AIJob)) == 0


def test_health_outdated_after_upload(db):
    ctx = drafted()
    cs = clients(ctx, db)
    lawyer = cs["lawyer"]
    lawyer.app.state.provider_factory = lambda settings, f: FakeProvider({"health": report_data()})
    r = lawyer.post(f"/api/rounds/{ctx['round_id']}/health")
    assert r.status_code == 202, r.text
    body = lawyer.get(f"/api/rounds/{ctx['round_id']}/health").json()
    assert body["job"]["status"] == "done" and body["report"]["outdated"] is False
    assert lawyer.get(f"/api/rounds/{ctx['round_id']}").json()["health_job"]["status"] == "done"
    assert upload(lawyer, str(ctx["case_id"]), "02_掃描.pdf", blank_pdf()).status_code == 200
    report = lawyer.get(f"/api/rounds/{ctx['round_id']}/health").json()["report"]
    assert report["outdated"] is True and report["pending_ocr"] == ["原證2 第1頁"]
    # 助理也可以執行健檢（設計 5.1）
    cs["assistant"].app.state.provider_factory = lambda settings, f: FakeProvider({"health": report_data()})
    assert cs["assistant"].post(f"/api/rounds/{ctx['round_id']}/health").status_code == 202


def test_health_rerun_replaces(db):
    ctx = drafted()
    run(ctx, report_data(), db)
    report, _, _ = run(ctx, report_data(items=[GAP], issues=["時效"]), db)
    assert [i["title"] for i in report["items"]] == ["缺少行車紀錄器影像"] and report["issues"] == ["時效"]
    with tenant_session(ctx["firm"]) as s:
        assert s.scalar(select(func.count()).select_from(HealthReport)) == 1


def test_health_other_firm_404(db):
    ctx = drafted()
    other = create_firm("乙事務所")
    create_member(other, "b@example.test")
    b = TestClient(create_app(db))
    assert login(b, "b@example.test").status_code == 200
    assert b.post(f"/api/rounds/{ctx['round_id']}/health").status_code == 404
    assert b.get(f"/api/rounds/{ctx['round_id']}/health").status_code == 404


def test_prompt_rules():
    from app.ai.prompts import load_prompt
    text = load_prompt("health")
    for rule in ("案件本身", "高風險", "補強建議", "證據缺口", "6 字", "other_references", "absence", "承認",
                 "預期爭點", "已證實"):
        assert rule in text


def test_health_forbidden_wording_softened(db):
    # critic #3：健檢標題、說明、爭點、不爭執事項都不得出現「已證實」「已驗證」
    ctx = drafted()
    item = {**HIGH, "title": "過失已證實", "explanation": "研判表已驗證被告未讓車"}
    undisputed = [{"fact": "事故已證實發生", "source": "對造書狀A", "page_no": 1, "quote": OPP_QUOTE}]
    report, _, _ = run(ctx, report_data(items=[item], undisputed=undisputed, issues=["責任已驗證？"]), db)
    text = repr(report)
    assert "已證實" not in text and "已驗證" not in text
    assert report["items"][0]["title"] == "過失可佐證"
