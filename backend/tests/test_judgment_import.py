"""子專案丙 Task 2：律師貼司法院判決網址，系統自己向司法院下載 PDF 加入判決庫。測試一律不連網。"""
import email.message
import io
import json
import threading
import urllib.error
import urllib.request
import urllib.response
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.corpus import importer
from app.corpus.importer import (
    ImportRejected, JudgmentRef, citation_from_text, parse_judgment_url, pdf_text, pdf_url,
)
from app.db import admin_session, corpus_session
from app.models import Judgment, JudgmentImport, JudgmentPassage
from tests.export_helpers import clients
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round

SEED = Path(__file__).resolve().parent.parent / "seed"
SEED_WITH_RAW = sorted(p.name for p in (SEED / "judgments").glob("*.json")
                       if json.loads(p.read_text(encoding="utf-8")).get("raw_file"))
TNDV = json.loads((SEED / "judgments" / "TNDV_114_su_1347.json").read_text(encoding="utf-8"))
URL_DATA = "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TNDV%2c114%2c%e8%a8%b4%2c1347%2c20260113%2c1&ot=in"
URL_FILE = "https://judgment.judicial.gov.tw/FILES/TNDV/114,訴,1347,20260113,1.pdf"
BAD_URL = "只接受司法院判決書系統的民事、家事判決網址"
FETCH_FAILED = "司法院網站沒有回傳判決 PDF，請確認網址或稍後再試"


def _raw(name: str) -> bytes:
    return (SEED / "raw" / name).read_bytes()


def test_parse_both_url_forms():
    a = parse_judgment_url(URL_DATA)
    b = parse_judgment_url(URL_FILE)
    assert a == b == JudgmentRef("TNDV", 114, "訴", 1347, date(2026, 1, 13), 1)
    assert a.key == b.key == "TNDV,114,訴,1347,20260113,1"
    assert pdf_url(a) == pdf_url(b) == "https://judgment.judicial.gov.tw/FILES/TNDV/114,%E8%A8%B4,1347,20260113,1.pdf"
    assert parse_judgment_url("https://judgment.judicial.gov.tw/FILES/TNDV/114%2c%e8%a8%b4%2c1347%2c20260113%2c1.pdf") == a


@pytest.mark.parametrize("url", [
    "http://judgment.judicial.gov.tw/FILES/TNDV/114,訴,1347,20260113,1.pdf",
    "https://judgment.judicial.gov.tw.evil.com/FILES/TNDV/114,訴,1347,20260113,1.pdf",
    "https://judgment.judicial.gov.tw@evil.com/FILES/TNDV/114,訴,1347,20260113,1.pdf",
    "https://judgment.judicial.gov.tw:8443/FILES/TNDV/114,訴,1347,20260113,1.pdf",
    "https://judgment.judicial.gov.tw/FILES/TNDM/114,訴,1347,20260113,1.pdf",
    "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=TNDM%2c114%2c%e8%a8%b4%2c1347%2c20260113%2c1",
    "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&ot=in",
    "https://judgment.judicial.gov.tw/FILES/TNDV/114,訴,1347,20261301,1.pdf",
    "not a url",
])
def test_import_rejects_bad_urls(url):
    with pytest.raises(ImportRejected) as e:
        parse_judgment_url(url)
    assert e.value.message == BAD_URL


class FakeHTTPS(urllib.request.HTTPSHandler):
    """取代真正的 HTTPS 連線；轉址仍由 opener 的轉址處理器決定要不要跟。"""

    def __init__(self, status=200, body=b"", headers=None, error=None):
        super().__init__()
        self.status, self.body, self.headers, self.error = status, body, headers or {}, error
        self.requests = []

    def https_open(self, req):
        self.requests.append(req)
        if self.error:
            raise self.error
        msg = email.message.Message()
        for k, v in self.headers.items():
            msg[k] = v
        resp = urllib.response.addinfourl(io.BytesIO(self.body), msg, req.full_url, self.status)
        resp.msg = "fake"
        return resp


REAL_OPENER = importer._opener  # 真正的 opener（含「不跟轉址」）；只把連線換成假的


def _use(monkeypatch, handler):
    monkeypatch.setattr(importer, "_opener", lambda: REAL_OPENER(handler))


def test_fetch_pdf_refuses_redirect_html_and_oversize(monkeypatch):
    url = pdf_url(parse_judgment_url(URL_FILE))
    redirect = FakeHTTPS(302, headers={"Location": "https://evil.example/x.pdf"})
    _use(monkeypatch, redirect)
    with pytest.raises(ImportRejected) as e:
        importer.fetch_pdf(url)
    assert e.value.message == FETCH_FAILED
    assert len(redirect.requests) == 1  # 沒有跟著轉址

    _use(monkeypatch, FakeHTTPS(200, b"<html>blocked</html>"))
    with pytest.raises(ImportRejected):
        importer.fetch_pdf(url)

    _use(monkeypatch, FakeHTTPS(500, b"%PDF-1.7"))
    with pytest.raises(ImportRejected):
        importer.fetch_pdf(url)

    _use(monkeypatch, FakeHTTPS(error=urllib.error.URLError("timed out")))
    with pytest.raises(ImportRejected):
        importer.fetch_pdf(url)

    assert importer.MAX_PDF_BYTES == 20_000_000
    monkeypatch.setattr(importer, "MAX_PDF_BYTES", 100)
    _use(monkeypatch, FakeHTTPS(200, b"%PDF-1.7" + b"0" * 200))
    with pytest.raises(ImportRejected):
        importer.fetch_pdf(url)

    ok = FakeHTTPS(200, b"%PDF-1.7" + b"0" * 50)
    _use(monkeypatch, ok)
    assert importer.fetch_pdf(url).startswith(b"%PDF")
    assert ok.requests[0].get_header("User-agent") == "legaltech-judgment-import/0.1"
    assert ok.requests[0].full_url == url


@pytest.mark.parametrize("seed_json", SEED_WITH_RAW)
def test_citation_matches_seed(seed_json):
    d = json.loads((SEED / "judgments" / seed_json).read_text(encoding="utf-8"))
    text = pdf_text(_raw(d["raw_file"]))
    assert text == d["full_text"]
    assert citation_from_text(text, parse_judgment_url(d["source_url"])) == d["citation"]


def test_branch_court_citation():
    tchv = JudgmentRef("TCHV", 113, "上", 100, date(2025, 1, 2), 1)
    tphv = JudgmentRef("TPHV", 113, "上", 100, date(2025, 1, 2), 1)
    assert citation_from_text("臺灣高等法院臺中分院民事判決\n113年度上字第100號\n上訴人", tchv) \
        == "臺灣高等法院臺中分院113年度上字第100號民事判決"
    assert tchv.key != tphv.key
    tcev = JudgmentRef("TCEV", 113, "中簡", 3211, date(2025, 1, 22), 1)
    assert citation_from_text("臺灣臺中地方法院臺中簡易庭民事判決\n113年度中簡字第3211號", tcev) \
        == "臺灣臺中地方法院113年度中簡字第3211號民事判決"
    tpev = JudgmentRef("TPEV", 114, "北簡", 610, date(2026, 4, 8), 1)
    assert citation_from_text("臺灣臺北地方法院民事簡易判決\n114年度北簡字第610號", tpev) \
        == "臺灣臺北地方法院114年度北簡字第610號民事簡易判決"
    with pytest.raises(ImportRejected) as e:
        citation_from_text("這不是判決書", tpev)
    assert e.value.message == "無法從判決內容判讀法院與字號，暫時不能加入"


def _fetcher(client, calls, data=None, error=None):
    def fetch(url):
        calls.append(url)
        if error:
            raise error
        return data if data is not None else _raw(TNDV["raw_file"])
    client.app.state.judgment_fetcher = fetch


def _count(model) -> int:
    with admin_session() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_import_from_pdf_creates_judgment_and_passages(lawyer_client):
    client, firm, member = lawyer_client
    case_id, _ = make_round(firm)
    calls = []
    _fetcher(client, calls)
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == ["https://judgment.judicial.gov.tw/FILES/TNDV/114,%E8%A8%B4,1347,20260113,1.pdf"]
    assert body["created"] is True
    assert body["judgment"] == {"citation": TNDV["citation"], "court": TNDV["court"], "decided_on": "2026-01-13"}
    assert body["passages"] and all(p["pin_id"] is None for p in body["passages"])
    with admin_session() as s:
        j = s.scalars(select(Judgment)).one()
        assert j.source_key == "TNDV,114,訴,1347,20260113,1"
        assert j.case_types == ["traffic_tort"] and j.curated is False and j.court_level == "district"
        assert j.source_url == calls[0]
        assert {s.get(JudgmentPassage, p["passage_id"]).passage_type for p in body["passages"]} == {"court_reasoning"}
        imp = s.scalars(select(JudgmentImport)).one()
        assert (imp.firm_id, imp.member_id, imp.case_id, imp.judgment_id, imp.url) == (firm, member, case_id, j.id, URL_DATA)
    r = client.post(f"/api/cases/{case_id}/pins", json={"passage_id": body["passages"][0]["passage_id"]})
    assert r.status_code == 200


def test_import_same_url_twice_no_duplicate(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    calls = []
    _fetcher(client, calls)
    first = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA}).json()
    second = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_FILE}).json()
    assert first["created"] is True and second["created"] is False
    assert second["judgment"] == first["judgment"]
    assert _count(Judgment) == 1 and _count(JudgmentImport) == 2
    assert len(calls) == 1  # 後端階段審查 #4：判決庫已有就不再向司法院下載


def test_migrations_hold_no_corpus_password():
    """後端階段審查 #3：migration 不寫死 corpus_writer 的密碼（公開 repo）；開發用密碼只在 init.sql。"""
    root = Path(__file__).resolve().parent.parent
    assert "corpuspass" not in (root / "migrations" / "versions" / "0009_judgment_import.py").read_text(encoding="utf-8")
    assert "corpuspass" in (root.parent / "init.sql").read_text(encoding="utf-8")


def test_concurrent_import_returns_existing(db, monkeypatch):
    ref = parse_judgment_url(URL_FILE)
    text = pdf_text(_raw(TNDV["raw_file"]))
    barrier = threading.Barrier(2, timeout=20)
    real = importer._find_by_key
    seen = threading.local()

    def gated(s, key):
        found = real(s, key)
        if not getattr(seen, "done", False):  # 兩邊都先查過「還沒有」，才開始寫入
            seen.done = True
            barrier.wait()
        return found

    monkeypatch.setattr(importer, "_find_by_key", gated)
    results, errors = [], []

    def run():
        try:
            with corpus_session() as cs:
                j, created = importer.import_judgment(cs, ref, text, "spousal_rights")
                results.append((j.id, created))
        except Exception as e:  # noqa: BLE001 - 測試要看到任何例外
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert len({jid for jid, _ in results}) == 1
    assert sorted(c for _, c in results) == [False, True]
    assert _count(Judgment) == 1


def test_import_non_pdf_or_unsplittable_writes_nothing(lawyer_client, monkeypatch):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    _fetcher(client, [], error=ImportRejected(FETCH_FAILED))
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "import_rejected", "message": FETCH_FAILED}

    _fetcher(client, [])
    monkeypatch.setattr(importer, "split_problems", lambda *a, **k: ["x"])
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 422
    assert "無法自動切成段落" in r.json()["detail"]["message"]

    _fetcher(client, [], data=b"%PDF-1.7 not really a pdf")
    r = client.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA})
    assert r.status_code == 422
    assert _count(Judgment) == 0 and _count(JudgmentImport) == 0


def test_import_bad_url_422_without_download(lawyer_client):
    client, firm, _ = lawyer_client
    case_id, _ = make_round(firm)
    calls = []
    _fetcher(client, calls)
    r = client.post(f"/api/cases/{case_id}/judgments/import",
                    json={"url": "https://judgment.judicial.gov.tw.evil.com/FILES/TNDV/114,訴,1347,20260113,1.pdf"})
    assert r.status_code == 422 and r.json()["detail"]["message"] == BAD_URL
    assert calls == []


def test_assistant_can_import(db):
    firm = create_firm("甲事務所")
    c = clients({"firm": firm}, db)["assistant"]
    case_id, _ = make_round(firm)
    _fetcher(c, [])
    assert c.post(f"/api/cases/{case_id}/judgments/import", json={"url": URL_DATA}).status_code == 200


def test_import_other_firm_case_404(db):
    from fastapi.testclient import TestClient

    from app.main import create_app
    firm_a, firm_b = create_firm("甲事務所"), create_firm("乙事務所")
    create_member(firm_a, "a@a.test")
    client = TestClient(create_app(db))
    assert login(client, "a@a.test").status_code == 200
    case_b, _ = make_round(firm_b)
    calls = []
    _fetcher(client, calls)
    assert client.post(f"/api/cases/{case_b}/judgments/import", json={"url": URL_DATA}).status_code == 404
    assert calls == [] and _count(Judgment) == 0
