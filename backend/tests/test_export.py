import io
import threading
import time

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from sqlalchemy import select, update

from app.db import tenant_session
from app.export import docx_export
from app.models import Citation, Document, Exhibit, ExportRecord, Paragraph
from tests.export_helpers import BAR, clients, force_reviewed
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

TEXT = "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。"


def ready(db):
    ctx = setup_verify_round()
    make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)], heading="㈠關於路權")
    cs = clients(ctx, db)
    return ctx, cs


def review_all(ctx, lawyer):
    with tenant_session(ctx["firm"]) as s:
        cid = s.scalars(select(Citation).where(Citation.code == "J1")).one().id
        from app.models import Paragraph
        pid = s.scalars(select(Paragraph.id).where(Paragraph.draft_id == ctx["draft_id"])).one()
    assert lawyer.post(f"/api/citations/{cid}/confirm").status_code == 200
    body = lawyer.get(f"/api/drafts/{ctx['draft_id']}").json()
    version = next(p["version"] for p in body["paragraphs"] if p["id"] == str(pid))
    r = lawyer.post(f"/api/paragraphs/{pid}/review", json={"version": version})
    assert r.status_code == 200, r.text
    return pid


def all_text(d: docx.Document) -> str:
    return "\n".join(p.text for p in d.paragraphs)


def test_export_blocked_when_unreviewed_409(db):
    ctx, cs = ready(db)
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409
    assert {"paragraph_seq": 1, "paragraph_no": 1, "reason": "unreviewed", "scope": "paragraph"} in r.json()["blockers"]


def test_assistant_export_403(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    assert cs["assistant"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 403


def test_export_docx_format(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")
    d = docx.Document(io.BytesIO(r.content))
    sec = d.sections[0]
    for m in (sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin):
        assert abs(m - Cm(2.5)) < Cm(0.01)  # Word 以 twip 儲存，2.5 cm = 1417 twip
    normal = d.styles["Normal"]
    assert normal.font.size == Pt(14)
    assert normal.element.rPr.rFonts.get(qn("w:eastAsia")) == "標楷體"
    assert normal.paragraph_format.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    # 司法院《民事訴訟資料標準化須知》：固定行高至少 25 點、每頁下方中間編頁；書狀規則第 3 條：A4
    assert normal.paragraph_format.line_spacing == Pt(25)
    assert normal.paragraph_format.space_after == Pt(0) and normal.paragraph_format.space_before == Pt(0)
    assert abs(sec.page_width - Cm(21)) < Cm(0.01) and abs(sec.page_height - Cm(29.7)) < Cm(0.01)
    footer = sec.footer.paragraphs[0]
    assert footer.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert "PAGE" in footer._p.xml and footer.text.startswith("第")
    title = d.paragraphs[0]
    assert title.text == "民事準備書狀"
    assert all(run.bold and run.font.size == Pt(18) for run in title.runs)


def test_export_has_no_markers_and_renders_exhibit(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    d = docx.Document(io.BytesIO(cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").content))
    text = all_text(d)
    assert "{{" not in text and "原證1第1頁" in text and "㈠關於路權" in text
    assert "臺灣臺中地方法院113年度中簡字第3946號民事判決" in text and f"「{J_REASONING}」" in text
    assert "證物名稱及件數" in text and "原證1" in text.split("證物名稱及件數")[1] and "原證1　研判表" in text


def test_export_uses_renumbered_exhibit(db):
    ctx, cs = ready(db)
    with tenant_session(ctx["firm"]) as s:
        ex = s.scalars(select(Exhibit).where(Exhibit.document_id == ctx["exhibit_doc"])).one().id
    assert cs["lawyer"].patch(f"/api/exhibits/{ex}", json={"number": 5}).status_code == 200
    review_all(ctx, cs["lawyer"])
    text = all_text(docx.Document(io.BytesIO(cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").content)))
    assert "原證5第1頁" in text and "原證1第1頁" not in text


def test_export_writes_record_without_content(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    import hashlib
    with tenant_session(ctx["firm"]) as s:
        rec = s.scalars(select(ExportRecord)).one()
        assert rec.bar_number == BAR and rec.sha256 == hashlib.sha256(r.content).hexdigest()
        assert rec.draft_id == ctx["draft_id"]


def test_force_reviewed_valid_draft_exports(db):
    """對照組：內容正確時，強制設成已審也能匯出（證明紅隊測試的 409 不是被 force_reviewed 害的）。"""
    ctx, cs = ready(db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    assert cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export").status_code == 200


def test_export_is_post_only(db):
    ctx, cs = ready(db)
    review_all(ctx, cs["lawyer"])
    assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}/export").status_code == 405


def test_empty_draft_cannot_be_exported(db):
    ctx = setup_verify_round()
    cs = clients(ctx, db)
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409
    assert r.json()["blockers"] == [{"paragraph_seq": 0, "paragraph_no": 0, "reason": "empty_draft", "scope": "draft"}]
    with tenant_session(ctx["firm"]) as s:
        assert s.scalars(select(ExportRecord)).first() is None


def test_control_chars_block_export_instead_of_500(db):
    ctx = setup_verify_round()
    make_paragraph(ctx, "被告應負賠償責任，此有{{cite:E1}}可稽。", [("E1", E_QUOTE)])
    cs = clients(ctx, db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 409
    assert {"paragraph_seq": 1, "paragraph_no": 1, "reason": "control_char", "scope": "paragraph"} in r.json()["blockers"]


def test_control_chars_in_filename_are_dropped_on_export(db):
    ctx, cs = ready(db)
    with tenant_session(ctx["firm"]) as s:
        s.execute(update(Document).where(Document.id == ctx["exhibit_doc"]).values(filename="01_研判表.txt"))
    review_all(ctx, cs["lawyer"])
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200, r.text
    assert "原證1　研判表" in all_text(docx.Document(io.BytesIO(r.content)))


def test_export_keeps_paragraphs_locked_until_done(db, monkeypatch):
    """檢查完阻擋原因到產生檔案之間，別人改段落要等匯出完成；匯出的必須是審過的那一版。"""
    ctx, cs = ready(db)
    pid = review_all(ctx, cs["lawyer"])
    checked = threading.Event()
    original = docx_export.export_blockers

    def slow_blockers(s, draft_id):
        out = original(s, draft_id)
        checked.set()
        time.sleep(0.6)
        return out

    def concurrent_edit():
        assert checked.wait(5)
        with tenant_session(ctx["firm"]) as s:
            s.execute(update(Paragraph).where(Paragraph.id == pid)
                      .values(text="被告應負全部責任。", status="unreviewed"))

    monkeypatch.setattr(docx_export, "export_blockers", slow_blockers)
    t = threading.Thread(target=concurrent_edit)
    t.start()
    r = cs["lawyer"].post(f"/api/drafts/{ctx['draft_id']}/export")
    t.join()
    assert r.status_code == 200, r.text
    assert "被告應負全部責任" not in all_text(docx.Document(io.BytesIO(r.content)))



# ---- 計畫 1B Task 9：依程序產生書狀名稱、受文與完整狀頭（設計 5.6） ----
from datetime import date  # noqa: E402
from urllib.parse import quote  # noqa: E402

from app.auth import CurrentMember  # noqa: E402
from app.export.docx_export import export_draft, roc_date  # noqa: E402
from app.models import Case, CaseParty, Member  # noqa: E402

DAY = date(2026, 11, 23)
ESTATE_PARTIES = [("宋文彬", "原告", "ours", None), ("宋文德", "被告", "opposing", "宋大德"),
                  ("宋文琪", "被告", "aligned", None)]


def test_roc_date():
    assert roc_date(date(2026, 11, 23)) == "中華民國115年11月23日"
    assert roc_date(date(2012, 1, 5)) == "中華民國101年1月5日"


def _set_case(ctx, parties=(), **fields):
    with tenant_session(ctx["firm"]) as s:
        case = s.get(Case, ctx["case_id"])
        for key, value in fields.items():
            setattr(case, key, value)
        for seq, (name, role, alignment, rep) in enumerate(parties, start=1):
            s.add(CaseParty(firm_id=ctx["firm"], case_id=ctx["case_id"], seq=seq, name=name, role=role,
                            alignment=alignment, legal_rep=rep))


def _export(ctx, db, heading="關於遺產範圍"):
    """一段已審（強制）的草稿，用固定日期匯出；回傳 (每段文字, 律師 client)。"""
    make_paragraph(ctx, TEXT, [("J1", J_REASONING), ("E1", E_QUOTE)], heading=heading)
    cs = clients(ctx, db)
    force_reviewed(ctx["firm"], ctx["draft_id"])
    with tenant_session(ctx["firm"]) as s:
        mid = s.scalars(select(Member.id).where(Member.email == "lawyer@a.test")).one()
        member = CurrentMember(id=mid, firm_id=ctx["firm"], role="lawyer", bar_number=BAR, bar_verified=True)
        data = export_draft(s, ctx["draft_id"], member, today=DAY)
    return [p.text for p in docx.Document(io.BytesIO(data)).paragraphs], cs["lawyer"]


def _in_order(lines, expected):
    idx = [lines.index(x) for x in expected]  # 每一項都要是完整的一段
    assert idx == sorted(idx), list(zip(expected, idx))


def test_family_case_header(db):
    ctx = setup_verify_round()
    _set_case(ctx, ESTATE_PARTIES, case_type="estate_partition", court="臺灣臺北地方法院",
              case_number="113年度家繼訴字第89號", division="忠", cause="分割遺產", attorneys=["王大明律師"])
    lines, _ = _export(ctx, db)
    text = "\n".join(lines)
    assert lines[:8] == ["家事準備書狀", "案號：113年度家繼訴字第89號　股別：忠", "原告　宋文彬", "訴訟代理人　王大明律師",
                         "被告　宋文德", "法定代理人　宋大德", "被告　宋文琪", "為分割遺產事件，依法提出準備書狀事："]
    assert lines.count("訴訟代理人　王大明律師") == 1
    _in_order(lines, ["原告　宋文彬", "訴訟代理人　王大明律師", "被告　宋文德", "法定代理人　宋大德", "被告　宋文琪",
                      "為分割遺產事件，依法提出準備書狀事：", "㈠關於遺產範圍", "謹狀", "臺灣臺北地方法院家事法庭　公鑒",
                      "證物名稱及件數", "原證1　研判表", "中華民國115年11月23日", "具狀人：宋文彬",
                      "撰狀人：訴訟代理人王大明律師"])
    assert "證物清單" not in text and "民事準備書狀" not in text and "民事庭" not in text


def test_case_number_overrides_type_procedure(db):
    ctx = setup_verify_round()
    _set_case(ctx, case_type="spousal_rights", court="臺灣臺北地方法院", case_number="113年度家訴字第5號")
    lines, lawyer = _export(ctx, db, heading="關於配偶權")
    assert lines[0] == "家事準備書狀" and "臺灣臺北地方法院家事法庭　公鑒" in lines
    r = lawyer.post(f"/api/drafts/{ctx['draft_id']}/export")
    assert r.status_code == 200 and quote("家事準備書狀.docx") in r.headers["content-disposition"]
    body = lawyer.get(f"/api/drafts/{ctx['draft_id']}").json()
    assert body["brief_title"] == "家事準備書狀" and "家訴" in body["procedure_warning"]


def test_addressee_override(db):
    ctx = setup_verify_round()
    _set_case(ctx, court="臺灣臺中地方法院", addressee="臺灣臺中地方法院臺中簡易庭")
    lines, _ = _export(ctx, db)
    assert "臺灣臺中地方法院臺中簡易庭　公鑒" in lines and "臺灣臺中地方法院民事庭　公鑒" not in lines


def test_empty_header_and_parties_export(db):
    ctx = setup_verify_round()
    _set_case(ctx, case_type="estate_partition")
    lines, lawyer = _export(ctx, db)
    assert lines[0] == "家事準備書狀"
    assert any(x.startswith("案號：") for x in lines) and any(x.startswith("具狀人：") for x in lines)
    assert "家事法庭　公鑒" in lines  # 沒填法院：受文只剩庭別，律師自己補
    assert lawyer.get(f"/api/drafts/{ctx['draft_id']}").json()["procedure_warning"] is None


def test_control_chars_in_header_dropped(db):
    ctx = setup_verify_round()
    _set_case(ctx, [("宋文\x0b彬", "原告", "ours", None)], case_type="estate_partition",
              case_number="113年度家繼訴字第\x0b89號", cause="分割\x0c遺產", attorneys=["王大明\x0b律師"])
    lines, _ = _export(ctx, db)
    text = "\n".join(lines)
    assert "原告　宋文彬" in lines and "案號：113年度家繼訴字第89號" in text and "為分割遺產事件，依法提出準備書狀事：" in lines
    assert "撰狀人：訴訟代理人王大明律師" in lines


def test_legacy_traffic_draft_still_exports(db):
    """計畫 1 的案件：沒有狀頭、沒有當事人 → 仍可匯出，民事準備書狀，我方位置留空白給律師填。"""
    ctx = setup_verify_round()
    lines, lawyer = _export(ctx, db, heading="㈠關於路權")
    assert lines[0] == "民事準備書狀" and "原告　" in lines and "被告　" in lines and "民事庭　公鑒" in lines
    _in_order(lines, ["原告　", "被告　", "㈠關於路權", "謹狀"])
    body = lawyer.get(f"/api/drafts/{ctx['draft_id']}").json()
    assert body["brief_title"] == "民事準備書狀" and body["procedure_warning"] is None


def test_counsel_listed_once_after_co_parties(db):
    """共同原告共用訴訟代理人：代理人列在連續的我方當事人之後，只列一次。"""
    ctx = setup_verify_round()
    _set_case(ctx, [("甲", "原告", "ours", None), ("乙", "原告", "ours", None), ("丙", "被告", "opposing", None)],
              attorneys=["王大明律師", "李小華律師"])
    lines, _ = _export(ctx, db)
    assert lines[2:7] == ["原告　甲", "原告　乙", "訴訟代理人　王大明律師", "訴訟代理人　李小華律師", "被告　丙"]
    assert "具狀人：甲、乙" in lines and "撰狀人：訴訟代理人王大明律師、李小華律師" in lines


def test_concurrent_exports_of_one_draft_do_not_deadlock(db, monkeypatch):
    """C10：兩個匯出同時讀同一份草稿，各自持有段落共享鎖後又都要寫驗證結果，會互等而死結；先鎖草稿列就會排隊。"""
    ctx, cs = ready(db)
    pid = review_all(ctx, cs["lawyer"])
    with tenant_session(ctx["firm"]) as s:  # 讓先前存的驗證結果過期：匯出重新驗證時一定要寫入
        s.execute(update(Paragraph).where(Paragraph.id == pid).values(verify_reasons=["stale_reason"]))
        member = CurrentMember(id=s.scalars(select(Member.id).where(Member.email == "lawyer@a.test")).one(),
                               firm_id=ctx["firm"], role="lawyer", bar_number=BAR, bar_verified=True)
    gate = threading.Barrier(2)
    original = docx_export.export_blockers

    def meet_then_verify(s, draft_id):
        try:
            gate.wait(2)  # 兩個匯出都拿到段落鎖之後才開始驗證（有排隊時第二個永遠到不了，逾時就放行）
        except threading.BrokenBarrierError:
            pass
        return original(s, draft_id)

    monkeypatch.setattr(docx_export, "export_blockers", meet_then_verify)
    errors: list[BaseException] = []

    def run():
        try:
            with tenant_session(ctx["firm"]) as s:
                export_draft(s, ctx["draft_id"], member)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    with tenant_session(ctx["firm"]) as s:
        assert len(s.scalars(select(ExportRecord).where(ExportRecord.draft_id == ctx["draft_id"])).all()) == 2


def test_draft_page_reload_waits_for_a_running_export(db):
    """草稿頁讀取會重新驗證並寫回結果，要跟匯出一樣先鎖草稿列排隊；
    否則匯出依段落 id 持共享鎖、讀取依段落順序寫入，兩邊順序不同會互等而死結。"""
    from app.models import Draft
    ctx, cs = ready(db)
    started, done = threading.Event(), threading.Event()
    result: dict = {}

    def reload_page():
        started.set()
        result["status"] = cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").status_code
        done.set()

    with tenant_session(ctx["firm"]) as s:
        s.get(Draft, ctx["draft_id"], with_for_update=True)  # 模擬匯出進行中：持有草稿列鎖
        t = threading.Thread(target=reload_page)
        t.start()
        started.wait(5)
        assert not done.wait(1.5), "匯出進行中，草稿頁讀取應該排隊等候"
    t.join(30)
    assert result["status"] == 200


def test_draft_page_reload_locks_paragraphs_before_writing_citations(db):
    """草稿頁讀取重新驗證時要先依段落 id 鎖住全部段落，再寫引用：
    其他寫入者（標記已審、修正、確認引用）都是先鎖段落再碰引用；反過來就會互等而死結。"""
    from sqlalchemy import event

    from app import db as dbmod
    ctx, cs = ready(db)
    with tenant_session(ctx["firm"]) as s:  # 讓先前存的驗證結果過期：讀取時一定要寫回
        s.execute(update(Citation).values(verify_reasons=["stale_reason"]))
        s.execute(update(Paragraph).values(verify_reasons=["stale_reason"]))
    seen: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        st = " ".join(statement.split())
        if "FROM paragraphs" in st and "FOR UPDATE" in st:
            seen.append("lock_paragraphs")
        elif st.startswith("UPDATE citations"):
            seen.append("update_citations")

    eng = dbmod.engine_for(dbmod.settings().database_url)
    event.listen(eng, "before_cursor_execute", record)
    try:
        assert cs["lawyer"].get(f"/api/drafts/{ctx['draft_id']}").status_code == 200
    finally:
        event.remove(eng, "before_cursor_execute", record)
    assert "update_citations" in seen  # 確實有寫回（否則這個測試沒有意義）
    assert seen.index("lock_paragraphs") < seen.index("update_citations")
