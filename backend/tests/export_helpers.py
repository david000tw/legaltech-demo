"""匯出測試共用：建立已登入的律師／助理，並可把整份草稿強制設成「全部審完、判決全部確認」。"""
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db import tenant_session
from app.main import create_app
from app.models import Citation, Paragraph
from tests.helpers import create_member, login

BAR = "(110)北律字第0001號"


def clients(ctx, settings):
    create_member(ctx["firm"], "lawyer@a.test", bar_number=BAR)
    create_member(ctx["firm"], "assistant@a.test", role="assistant")
    out = {}
    for who in ("lawyer", "assistant"):
        c = TestClient(create_app(settings))
        assert login(c, f"{who}@a.test").status_code == 200
        out[who] = c
    return out


def force_reviewed(firm, draft_id):
    """繞過審核規則，模擬「律師已全部審完並確認」——用來證明驗證仍會擋下匯出。"""
    with tenant_session(firm) as s:
        pids = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == draft_id)).all()
        s.execute(update(Paragraph).where(Paragraph.id.in_(pids)).values(status="reviewed"))
        s.execute(update(Citation).where(Citation.paragraph_id.in_(pids)).values(lawyer_confirmed=True))
