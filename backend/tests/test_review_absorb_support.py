"""按「已審」一併接受「部分支持」（設計 5.1）；紅色支持度仍要單獨處理。"""
import re

import pytest
from sqlalchemy import select

from app.auth import CurrentMember
from app.db import tenant_session
from app.models import Citation, Member, ReviewEvent, SupportCheck
from app.review.service import ACCEPT_REASON, ReviewBlocked, mark_reviewed
from tests.export_helpers import BAR, clients
from tests.fakes.fake_provider import FakeProvider
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

pytestmark = pytest.mark.support_real

TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"
_BLOCK = re.compile(r"【引用 (C\d+)】(.*?)(?=【引用 C\d+】|\Z)", re.S)


def _fake(e1_verdict):
    def handler(call):
        items = []
        for code, block in _BLOCK.findall(call["user"]):
            quote = block.split("【引文】", 1)[1].split("\n", 1)[0]
            items.append({"id": code, "verdict": e1_verdict if quote == E_QUOTE else "supports",
                          "explanation": "研判表只提到未讓幹道車先行"})
        return {"items": items}
    return FakeProvider({"support": handler})


def _setup(db, e1_verdict):
    ctx = setup_verify_round()
    c = clients(ctx, db)
    fake = _fake(e1_verdict)
    for client in c.values():
        client.app.state.provider_factory = lambda settings, firm: fake
    pid = make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)])
    c["lawyer"].get(f"/api/drafts/{ctx['draft_id']}")  # 排入並跑完支持度檢查（TestClient 同步執行背景工作）
    return ctx, c, pid


def _cite(ctx, pid, code):
    with tenant_session(ctx["firm"]) as s:
        return s.scalars(select(Citation).where(Citation.paragraph_id == pid, Citation.code == code)).one().id


def _review(ctx, client, pid, *, seen=True, override=None):
    """seen：照畫面上顯示的支持度版本送回（律師看過說明）；override 換掉指定引用的版本。"""
    body = client.get(f"/api/drafts/{ctx['draft_id']}").json()
    para = next(p for p in body["paragraphs"] if p["id"] == str(pid))
    payload = {"version": para["version"]}
    if seen:
        payload["support_versions"] = {c["code"]: c["support"]["version"] for c in para["citations"]
                                       if c.get("support") and c["support"].get("version")}
        payload["support_versions"].update(override or {})
    return client.post(f"/api/paragraphs/{pid}/review", json=payload)


def _e1_row(ctx, pid):
    with tenant_session(ctx["firm"]) as s:
        rows = s.scalars(select(SupportCheck).where(SupportCheck.case_id == ctx["case_id"])).all()
        return next(r for r in rows if r.quote == E_QUOTE)


def test_review_accepts_partial_support(db):
    ctx, c, pid = _setup(db, "partial")
    assert _e1_row(ctx, pid).verdict == "partial"
    c["lawyer"].get(f"/api/citations/{_cite(ctx, pid, 'J1')}/source")
    r = _review(ctx, c["lawyer"], pid)
    assert r.status_code == 200, r.text
    row = _e1_row(ctx, pid)
    assert row.released_at is not None and row.release_reason == ACCEPT_REASON and row.released_bar_number == BAR
    with tenant_session(ctx["firm"]) as s:
        ev = s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == pid,
                                                 ReviewEvent.action == "release_support")).one()
        assert "E1" in ev.after_text and ACCEPT_REASON in ev.after_text and BAR in ev.after_text


def test_partial_not_absorbed_when_disabled(db):
    ctx, c, pid = _setup(db, "partial")
    c["lawyer"].get(f"/api/citations/{_cite(ctx, pid, 'J1')}/source")
    version = next(p["version"] for p in c["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").json()["paragraphs"])
    with tenant_session(ctx["firm"]) as s:
        mid = s.scalars(select(Member.id).where(Member.email == "lawyer@a.test")).one()
        member = CurrentMember(id=mid, firm_id=ctx["firm"], role="lawyer", bar_number=BAR, bar_verified=True)
        with pytest.raises(ReviewBlocked) as e:
            mark_reviewed(s, pid, member, version)  # 沒帶畫面上的支持度版本
    assert e.value.reasons == ["support_partial:E1"]


def test_red_support_still_blocks(db):
    ctx, c, pid = _setup(db, "not_supported")
    c["lawyer"].get(f"/api/citations/{_cite(ctx, pid, 'J1')}/source")
    r = _review(ctx, c["lawyer"], pid)
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["support_not_supported:E1"]
    assert _e1_row(ctx, pid).released_at is None


def test_blocked_review_writes_nothing(db):
    ctx, c, pid = _setup(db, "not_supported")
    j1 = _cite(ctx, pid, "J1")
    c["lawyer"].get(f"/api/citations/{j1}/source")
    assert _review(ctx, c["lawyer"], pid).status_code == 409
    with tenant_session(ctx["firm"]) as s:
        assert not s.get(Citation, j1).lawyer_confirmed
        assert s.scalars(select(ReviewEvent).where(ReviewEvent.paragraph_id == pid)).all() == []


def test_partial_not_absorbed_without_seen_version(db):
    """審查 #3：沒帶回畫面上的支持度版本（例如舊畫面、別的入口）就不一併接受部分支持。"""
    ctx, c, pid = _setup(db, "partial")
    c["lawyer"].get(f"/api/citations/{_cite(ctx, pid, 'J1')}/source")
    r = _review(ctx, c["lawyer"], pid, seen=False)
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["support_partial:E1"]
    assert _e1_row(ctx, pid).released_at is None


def test_partial_not_absorbed_when_result_changed(db):
    """畫面上的結果已經過時（例如別人按了重新檢查）：不能用舊的那一版接受。"""
    ctx, c, pid = _setup(db, "partial")
    c["lawyer"].get(f"/api/citations/{_cite(ctx, pid, 'J1')}/source")
    r = _review(ctx, c["lawyer"], pid, override={"E1": "0000000000000000"})
    assert r.status_code == 409 and r.json()["detail"]["reasons"] == ["support_partial:E1"]
    assert _e1_row(ctx, pid).released_at is None
