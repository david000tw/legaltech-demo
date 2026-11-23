from sqlalchemy import select

from app.ai.prompts import load_prompt
from app.ai.schemas import CLAIMS_SCHEMA
from app.db import tenant_session
from app.ingest.service import ingest_document
from app.models import Page, UsageRecord
from app.pipeline.claims import extract_claims, format_pages
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round

VERBATIM = "原告行經無號誌路口時亦未減速慢行，未作隨時停車之準備"
PARAPHRASED = "原告請求的精神慰撫金十五萬元實在太高了，應該減少"


def claims_response(*items):
    return {"claims": [{"summary": s, "quote": q, "page_no": p} for s, q, p in items]}


def test_format_pages():
    assert format_pages([(1, "甲"), (2, "乙")]) == "【第1頁】\n甲\n\n【第2頁】\n乙"


def test_extract_claims_verifies_quotes(db):
    firm = create_firm("甲事務所")
    _, round_id = make_round(firm)
    fake = FakeProvider({"claims": claims_response(("原告與有過失", VERBATIM, 1),
                                                   ("慰撫金過高", PARAPHRASED, 2))})
    with tenant_session(firm) as s:
        claims = extract_claims(s, round_id, fake, None)
        assert [c.seq for c in claims] == [1, 2]
        assert claims[0].quote_verified is True
        assert claims[1].quote_verified is False
        p1 = s.get(Page, claims[0].page_id)
        assert p1.page_no == 1
    call = fake.calls[0]
    assert call["effort"] == "medium" and call["schema"] == CLAIMS_SCHEMA
    assert "【第1頁】" in call["user"] and "【第2頁】" in call["user"]
    assert call["system"] == load_prompt("claims")


def test_extract_claims_bad_page_no_unverified(db):
    firm = create_firm("甲事務所")
    _, round_id = make_round(firm)
    fake = FakeProvider({"claims": claims_response(("原告與有過失", VERBATIM, 9))})
    with tenant_session(firm) as s:
        (c,) = extract_claims(s, round_id, fake, None)
        assert c.quote_verified is False and c.page_id is None


def test_extract_claims_short_quote_unverified(db):
    firm = create_firm("甲事務所")
    _, round_id = make_round(firm)
    fake = FakeProvider({"claims": claims_response(("原告與有過失", "未減速慢行", 1))})
    with tenant_session(firm) as s:
        (c,) = extract_claims(s, round_id, fake, None)
        assert c.quote_verified is False


def test_extract_claims_records_usage(db):
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm)
    fake = FakeProvider({"claims": claims_response(("原告與有過失", VERBATIM, 1))})
    with tenant_session(firm) as s:
        extract_claims(s, round_id, fake, None)
        s.flush()
        rec = s.scalars(select(UsageRecord)).one()
        assert rec.step == "claims" and rec.case_id == case_id and rec.input_tokens == 1


def test_pages_numbered_across_briefs_and_scans_skipped(db):
    """同一輪有兩份對造書狀時，頁碼連續編號；沒有文字層的頁不送給 AI。"""
    firm = create_firm("甲事務所")
    case_id, round_id = make_round(firm)
    with tenant_session(firm) as s:
        ingest_document(s, firm, case_id, "opponent_brief", "補充答辯狀.txt",
                        "補充：原告之車速明顯超過速限每小時五十公里，應負較高之過失比例。".encode(), round_id)
    second = "原告之車速明顯超過速限每小時五十公里"
    fake = FakeProvider({"claims": claims_response(("原告超速", second, 3))})
    with tenant_session(firm) as s:
        (c,) = extract_claims(s, round_id, fake, None)
        assert c.quote_verified is True
    assert "【第3頁】" in fake.calls[0]["user"]


def test_claims_prompt_has_required_rules():
    p = load_prompt("claims")
    for rule in ("不加評論", "逐字", "15", "只能對應一頁", "繁體中文"):
        assert rule in p
