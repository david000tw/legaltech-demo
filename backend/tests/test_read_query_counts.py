"""讀取端點每 3 秒被輪詢一次：查詢次數不能隨頁數增加（300 頁的案件不能每次跑幾百次查詢）。"""
from sqlalchemy import event, select

from app import db as dbmod
from app.db import tenant_session
from app.models import Document, Page, PageText
from app.ingest.service import current_page_text, document_page_texts, document_text_ids
from app.pipeline.evidence_notes import documents_needing_notes, notes_view
from app.pipeline.health import report_view
from tests.helpers import create_firm
from tests.pipeline_helpers import make_round
from tests.test_health_check import drafted, report_data, run


def _count_statements(fn) -> int:
    eng = dbmod.engine_for(dbmod.settings().database_url)
    seen: list[str] = []

    def listener(conn, cur, stmt, params, ctx, many):
        seen.append(stmt)
    event.listen(eng, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(eng, "before_cursor_execute", listener)
    return len(seen)


def _add_pages(firm, document_id, start_no: int, n: int) -> None:
    """直接替既有文件補 n 頁（頁碼從 start_no 起，每頁一個文字版本）。"""
    with tenant_session(firm) as s:
        for no in range(start_no, start_no + n):
            page = Page(firm_id=firm, document_id=document_id, page_no=no, needs_ocr=False, ocr_state="none")
            s.add(page)
            s.flush()
            s.add(PageText(firm_id=firm, page_id=page.id, version=1, source="text_layer", text=f"第{no}頁的內文"))


def _evidence_doc_id(firm, case_id):
    with tenant_session(firm) as s:
        return s.scalars(select(Document).where(Document.case_id == case_id, Document.kind == "evidence")).one().id


def test_notes_view_statement_count_does_not_grow_with_pages(db):
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "第1頁\f第2頁"})
    doc_id = _evidence_doc_id(firm, case_id)

    def read():
        with tenant_session(firm) as s:
            notes_view(s, case_id)
            documents_needing_notes(s, case_id)

    small = _count_statements(read)
    _add_pages(firm, doc_id, 3, 18)  # 2 頁 → 20 頁
    large = _count_statements(read)
    assert large == small, f"查詢次數隨頁數增加：2 頁 {small} 次、20 頁 {large} 次"


def test_health_report_view_statement_count_does_not_grow_with_pages(db):
    ctx = drafted()
    run(ctx, report_data(), db)
    firm = ctx["firm"]

    def read():
        with tenant_session(firm) as s:
            assert report_view(s, ctx["round_id"]) is not None

    _add_pages(firm, ctx["exhibit_doc"], 2, 1)  # 1 頁 → 2 頁
    small = _count_statements(read)
    _add_pages(firm, ctx["exhibit_doc"], 3, 18)  # 2 頁 → 20 頁
    large = _count_statements(read)
    assert large == small, f"查詢次數隨頁數增加：2 頁 {small} 次、20 頁 {large} 次"


def test_batched_page_texts_equal_per_page_current_text(db):
    """一次查完的結果要與每頁各呼叫 current_page_text 完全一樣：多版本取最大版、沒有文字的頁是 None、依頁碼排序。"""
    firm = create_firm("甲事務所")
    case_id, _ = make_round(firm, evidence={"01_收據.txt": "第1頁第2頁第3頁"})
    doc_id = _evidence_doc_id(firm, case_id)
    with tenant_session(firm) as s:
        pages = list(s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)))
        s.add(PageText(firm_id=firm, page_id=pages[0].id, version=2, source="manual", text="第1頁（改）"))
        s.add(PageText(firm_id=firm, page_id=pages[0].id, version=3, source="manual", text="第1頁（再改）"))
        for pt in s.scalars(select(PageText).where(PageText.page_id == pages[2].id)):
            s.delete(pt)  # 第 3 頁沒有任何文字版本（待辨識）
    with tenant_session(firm) as s:
        expected = [(p.id, current_page_text(s, p.id)) for p in s.scalars(
            select(Page).where(Page.document_id == doc_id).order_by(Page.page_no))]
        got = document_page_texts(s, doc_id)
        ids = document_text_ids(s, doc_id)
    assert [(p.id, pt.id if pt else None) for p, pt in got] == [(pid, pt.id if pt else None) for pid, pt in expected]
    assert ids == [(pid, pt.id if pt else None) for pid, pt in expected]
    assert got[0][1].version == 3 and got[2][1] is None
