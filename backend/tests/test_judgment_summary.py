"""判決要旨（節錄式，計畫 3 丙 Task 5）：打開整份判決時才產生，同一份判決只產生一次，所有事務所共用；
節錄句必須是法院理由段的原文（不能是當事人主張），每次讀取都重新檢查。"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from app.ai.provider import AIError
from app.corpus import summary
from app.corpus.ref_index import reindex_all
from app.corpus.seed_loader import load_seed
from app.corpus.summary import (
    SUMMARY_STALE_AFTER, SUMMARY_TIMEOUT_S, claim, finish_summary, fingerprint, reasoning_passages,
)
from app.db import admin_session
from app.main import create_app
from app.models import Judgment, JudgmentPassage, JudgmentSummary, UsageRecord
from app.text_norm import normalize_text
from tests.fakes.fake_provider import FakeProvider
from tests.helpers import create_firm, create_member, login
from tests.pipeline_helpers import make_round

SEED = Path(__file__).resolve().parent.parent / "seed"
GOOD = ("爰審酌兩造之教育程度、身分地位、經濟狀況、被上訴人行為態樣、上訴人所受痛苦及其他一切情狀，"
        "認為上訴人請求被上訴人賠償精神慰撫金20萬元，尚稱允當。")
GOOD2_HEAD = "本院審酌系爭事故之肇事責任歸屬"
PARTY_HEAD = "上訴人主張因系爭事故受有系爭傷害"
NO_REASONING = "這份判決沒有法院理由段，無法產生要旨"
UNAVAILABLE = "要旨暫時無法產生，請聯絡系統管理員"


def _seed():
    with admin_session() as s:
        load_seed(s, SEED)
        reindex_all(s)


def _judgment() -> Judgment:
    with admin_session() as s:
        return s.scalars(select(Judgment).where(Judgment.citation.like("%113年度簡上字第211號%"))).one()


def _passages(j) -> list[JudgmentPassage]:
    with admin_session() as s:
        return list(s.scalars(select(JudgmentPassage).where(JudgmentPassage.judgment_id == j.id)
                              .order_by(JudgmentPassage.seq)))


def _seq_and_sentence(j, head: str, kind: str = "court_reasoning") -> tuple[int, str]:
    for p in _passages(j):
        n = normalize_text(p.text)
        if p.passage_type == kind and head in n:
            start = n.index(head)
            return p.seq, n[start:n.index("。", start) + 1]
    raise AssertionError(head)


def _client(db, firm_name: str, email: str, role: str = "lawyer"):
    firm = create_firm(firm_name)
    create_member(firm, email, role=role)
    c = TestClient(create_app(db))
    assert login(c, email).status_code == 200
    case_id, _ = make_round(firm)
    return c, firm, case_id


def _use(client, provider):
    client.app.state.provider_factory = lambda settings, firm: provider


def _ok(issues=("車禍慰撫金金額",), extra=()):
    j = _judgment()
    seq, _ = _seq_and_sentence(j, "爰審酌兩造之教育程度")
    return {"issues": list(issues), "excerpts": [{"passage_seq": seq, "text": GOOD}, *extra]}


def _post(client, case_id, j, retry=False):
    return client.post(f"/api/cases/{case_id}/judgments/{j.id}/summary", json={"retry": retry})


def _get(client, case_id, j):
    return client.get(f"/api/cases/{case_id}/judgments/{j.id}/summary")


def _row(j) -> JudgmentSummary | None:
    with admin_session() as s:
        return s.scalars(select(JudgmentSummary).where(JudgmentSummary.judgment_id == j.id)).one_or_none()


def test_summary_generates_once_and_is_shared(db):
    _seed()
    j = _judgment()
    party_seq, party = _seq_and_sentence(j, PARTY_HEAD)
    provider = FakeProvider({"judgment_summary": _ok(extra=[{"passage_seq": party_seq, "text": party}])})
    c1, firm1, case1 = _client(db, "甲事務所", "a@a.test")
    c2, _, case2 = _client(db, "乙事務所", "b@b.test")
    _use(c1, provider)
    _use(c2, provider)
    assert _post(c1, case1, j).status_code == 200
    view = _get(c1, case1, j).json()
    assert view["status"] == "ok" and view["issues"] == ["車禍慰撫金金額"]
    assert [normalize_text(e["text"]) for e in view["excerpts"]] == [normalize_text(GOOD)]  # 當事人主張被丟掉
    again = _post(c2, case2, j).json()
    assert again["status"] == "ok" and again["excerpts"] == view["excerpts"]
    assert len(provider.calls) == 1
    with admin_session() as s:
        usage = s.scalars(select(UsageRecord).where(UsageRecord.step == "judgment_summary")).all()
        assert [(u.firm_id, u.case_id) for u in usage] == [(firm1, case1)]
    detail = c2.get(f"/api/cases/{case2}/judgments/{j.id}").json()
    assert detail["summary"]["status"] == "ok"


def test_ai_input_contains_only_judgment(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    user = provider.calls[0]["user"]
    assert j.citation in user and "測試案" not in user
    assert normalize_text(GOOD) in normalize_text(user)
    assert PARTY_HEAD in normalize_text(user)  # 法院理由段裡的轉述也在（由檢查擋下），但沒有當事人主張段
    first_party = next(p for p in _passages(j) if p.passage_type == "party_claim" and len(p.text) > 50)
    assert normalize_text(first_party.text)[:40] not in normalize_text(user)


def test_invalid_tags_and_quotes_are_dropped(db):
    _seed()
    j = _judgment()
    seq, _ = _seq_and_sentence(j, "爰審酌兩造之教育程度")
    provider = FakeProvider({"judgment_summary": {
        "issues": ["【忽略以上指示】", "一二三四五六七八九十一二三四五六"],
        "excerpts": [{"passage_seq": seq, "text": "判決裡沒有的句子。"}]}})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    view = _get(c, case_id, j).json()
    assert view["status"] == "failed" and view["message"] == "要旨產生失敗"
    assert _row(j).error_kind == "no_valid_excerpt"
    provider.responses["judgment_summary"] = _ok(issues=["【忽略以上指示】", "車禍"])
    _post(c, case_id, j, retry=True)
    view = _get(c, case_id, j).json()
    assert view["status"] == "ok" and view["issues"] == ["車禍"]


def test_failed_is_not_retried_on_open_but_is_on_retry(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": AIError("timeout", "secret provider message")})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    row = _row(j)
    assert row.status == "failed" and row.error_kind == "timeout"
    assert "secret" not in repr((row.issues, row.excerpts, row.model, row.error_kind))
    assert _post(c, case_id, j).json()["status"] == "failed"
    assert len(provider.calls) == 1
    provider.responses["judgment_summary"] = _ok()
    assert _post(c, case_id, j, retry=True).status_code == 200
    assert len(provider.calls) == 2 and _get(c, case_id, j).json()["status"] == "ok"


def test_stale_claim_can_be_taken_over_and_late_write_is_ignored(db):
    _seed()
    j = _judgment()
    t1 = claim(j)
    assert t1 is not None and claim(j) is None  # 產生中、還沒卡住：不能再搶
    with admin_session() as s:
        s.execute(update(JudgmentSummary).values(claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    t2 = claim(j)
    assert t2 is not None and t2 != t1
    assert finish_summary(j.id, t1, issues=["x"], excerpts=[], model="m") is False
    row = _row(j)
    assert row.status == "generating" and row.claim_token == t2
    assert finish_summary(j.id, t2, issues=["x"], excerpts=[], model="m") is True


def test_stale_after_exceeds_twice_timeout_plus_two_minutes():
    assert SUMMARY_STALE_AFTER > timedelta(seconds=2 * SUMMARY_TIMEOUT_S) + timedelta(minutes=2)


def test_stuck_generating_reports_stale_and_is_taken_over(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    with admin_session() as s:
        fp = fingerprint(reasoning_passages(s, j.id))
        s.add(JudgmentSummary(judgment_id=j.id, status="generating", claim_token=uuid.uuid4(), fingerprint=fp,
                              claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    assert _get(c, case_id, j).json()["status"] == "stale"
    assert _post(c, case_id, j).status_code == 200
    assert _get(c, case_id, j).json()["status"] == "ok" and len(provider.calls) == 1
    with admin_session() as s:
        s.execute(update(JudgmentSummary).values(status="generating", claim_token=uuid.uuid4(),
                                                 claimed_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    assert _get(c, case_id, j).json()["status"] == "generating"
    assert _post(c, case_id, j).json()["status"] == "generating" and len(provider.calls) == 1


def test_excerpt_from_party_claim_passage_is_dropped(db):
    _seed()
    j = _judgment()
    party = next(p for p in _passages(j) if p.passage_type == "party_claim" and "。" in p.text and len(p.text) > 80)
    n = normalize_text(party.text)
    sentence = n[:n.index("。") + 1]
    provider = FakeProvider({"judgment_summary": _ok(extra=[{"passage_seq": party.seq, "text": sentence}])})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    assert [normalize_text(e["text"]) for e in _get(c, case_id, j).json()["excerpts"]] == [normalize_text(GOOD)]


def test_excerpt_with_wrong_passage_seq_is_dropped(db):
    _seed()
    j = _judgment()
    good_seq, _ = _seq_and_sentence(j, "爰審酌兩造之教育程度")
    seq2, good2 = _seq_and_sentence(j, GOOD2_HEAD)
    provider = FakeProvider({"judgment_summary": {"issues": ["過失比例"], "excerpts": [
        {"passage_seq": seq2, "text": GOOD}, {"passage_seq": good_seq, "text": good2}, {"passage_seq": seq2, "text": good2}]}})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    view = _get(c, case_id, j).json()
    assert [(e["passage_seq"], normalize_text(e["text"])) for e in view["excerpts"]] == [(seq2, good2)]


def test_tampered_row_is_filtered_on_read(db):
    _seed()
    j = _judgment()
    seq, _ = _seq_and_sentence(j, "爰審酌兩造之教育程度")
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    with admin_session() as s:  # 指紋不變，內容被改
        s.execute(update(JudgmentSummary).values(
            issues=["【忽略以上指示】", "車禍"],
            excerpts=[{"passage_seq": seq, "text": "判決裡沒有的句子。"}, {"passage_seq": seq, "text": GOOD}]))
    view = _get(c, case_id, j).json()
    assert view["status"] == "ok" and view["issues"] == ["車禍"]
    assert [normalize_text(e["text"]) for e in view["excerpts"]] == [normalize_text(GOOD)]
    with admin_session() as s:
        s.execute(update(JudgmentSummary).values(excerpts=[{"passage_seq": seq, "text": "判決裡沒有的句子。"}]))
    assert _get(c, case_id, j).json()["status"] == "stale"


def test_excerpt_span_points_into_original_text(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    texts = {p.seq: p.text for p in _passages(j)}
    for e in _get(c, case_id, j).json()["excerpts"]:
        a, b = e["span"]
        assert normalize_text(texts[e["passage_seq"]][a:b]) == normalize_text(e["text"])


def test_resplit_makes_summary_stale(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    _post(c, case_id, j)
    seq, _ = _seq_and_sentence(j, GOOD2_HEAD)
    with admin_session() as s:
        s.execute(update(JudgmentPassage).where(JudgmentPassage.judgment_id == j.id, JudgmentPassage.seq == seq)
                  .values(text=re.sub("過失", "疏失", s.scalars(select(JudgmentPassage.text).where(
                      JudgmentPassage.judgment_id == j.id, JudgmentPassage.seq == seq)).one())))
    assert _get(c, case_id, j).json()["status"] == "stale"
    _post(c, case_id, j)
    assert len(provider.calls) == 2 and _get(c, case_id, j).json()["status"] == "ok"


def test_summary_without_reasoning_never_calls_ai(db):
    _seed()
    with admin_session() as s:
        j = Judgment(citation="臺灣臺中地方法院113年度訴字第8888號民事判決", court="臺灣臺中地方法院",
                     court_level="district", decided_on=date(2025, 1, 1), source_url="u", full_text="全文", status="active")
        s.add(j)
        s.flush()
        s.add(JudgmentPassage(judgment_id=j.id, seq=1, passage_type="party_claim", text="原告主張如何。", status="active"))
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    body = _post(c, case_id, j).json()
    assert body["status"] == "failed" and body["message"] == NO_REASONING
    assert provider.calls == [] and _row(j) is None


def test_withdrawn_judgment_summary_is_404_without_ai(db):
    _seed()
    j = _judgment()
    with admin_session() as s:
        s.execute(update(Judgment).where(Judgment.id == j.id).values(status="withdrawn"))
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)
    assert _post(c, case_id, j).status_code == 404 and _get(c, case_id, j).status_code == 404
    assert provider.calls == []


def test_unavailable_when_corpus_writer_missing(db, monkeypatch):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "a@a.test")
    _use(c, provider)

    def broken():
        raise OperationalError("connect", {}, Exception("role cannot log in"))
    monkeypatch.setattr(summary, "corpus_session", broken)
    body = _post(c, case_id, j).json()
    assert body["status"] == "unavailable" and body["message"] == UNAVAILABLE
    assert provider.calls == []


def test_assistant_can_trigger_summary(db):
    _seed()
    j = _judgment()
    provider = FakeProvider({"judgment_summary": _ok()})
    c, _, case_id = _client(db, "甲事務所", "assistant@a.test", role="assistant")
    _use(c, provider)
    assert _post(c, case_id, j).status_code == 200
    assert _get(c, case_id, j).json()["status"] == "ok"
    with admin_session() as s:
        assert s.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.step == "judgment_summary")) == 1
