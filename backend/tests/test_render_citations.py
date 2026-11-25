"""引用標記的顯示：接在句尾當出處的引用自動加括號（判決、法條加「參照」），
句中當成文字一部分的（依民法…、此有原證1…可稽）照原樣。走查時律師看到「…為肇事次因原證1第1頁。」很怪。"""
import pytest

from app.db import tenant_session
from app.models import Paragraph
from app.render import render_text
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

CITES = [("J1", J_REASONING), ("E1", E_QUOTE), ("S1", "")]


@pytest.fixture
def labels(db):
    ctx = setup_verify_round()
    pid = make_paragraph(ctx, "依{{cite:E1}}、依{{cite:J1}}、依{{cite:S1}}規定", CITES, seq=9)  # 句中用法：不加括號
    with tenant_session(ctx["firm"]) as s:
        e, j, st = render_text(s, s.get(Paragraph, pid)).removesuffix("規定").split("、")
    return ctx, e.removeprefix("依"), j.removeprefix("依"), st.removeprefix("依")


def _render(ctx, text, seq=1):
    pid = make_paragraph(ctx, text, CITES, seq=seq)
    with tenant_session(ctx["firm"]) as s:
        return render_text(s, s.get(Paragraph, pid))


def test_reference_at_sentence_end_gets_parentheses(labels):
    ctx, e, j, st = labels
    assert e == "原證1第1頁"
    assert _render(ctx, "被告未讓幹道車先行{{cite:E1}}。") == f"被告未讓幹道車先行（{e}）。"
    assert _render(ctx, "被告應負過失責任{{cite:J1}}；", 2) == f"被告應負過失責任（{j}意旨參照）；"
    assert _render(ctx, "駕駛人應減速慢行{{cite:S1}}，", 3) == f"駕駛人應減速慢行（{st}參照），"
    assert _render(ctx, "被告有過失{{cite:E1}}", 4) == f"被告有過失（{e}）"  # 段落結尾也算句尾


def test_quote_followed_by_its_source(labels):
    ctx, e, _, _ = labels
    assert _render(ctx, "研判表載明{{quote:E1}}{{cite:E1}}。") == f"研判表載明「{E_QUOTE}」（{e}）。"


def test_adjacent_references_share_one_pair_of_parentheses(labels):
    ctx, e, j, st = labels
    assert _render(ctx, "被告應負責{{cite:S1}}{{cite:J1}}。") == f"被告應負責（{st}、{j}意旨參照）。"
    assert _render(ctx, "被告有過失{{cite:E1}}{{cite:J1}}。", 2) == f"被告有過失（{e}；{j}意旨參照）。"


@pytest.mark.parametrize("text,expected", [
    ("依{{cite:S1}}規定，被告應負責。", "依{st}規定，被告應負責。"),
    ("按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。", "按{j}認被告「" + J_REASONING + "」，此有{e}可稽。"),
    ("被告應依{{cite:S1}}。", "被告應依{st}。"),
    ("被告應負責（{{cite:J1}}參照）。", "被告應負責（{j}參照）。"),  # AI 自己加了括號：不重複
    ("詳如{{cite:E1}}。", "詳如{e}。"),
    ("{{cite:S1}}定有明文。", "{st}定有明文。"),
])
def test_citations_used_as_part_of_the_sentence_stay_as_is(labels, text, expected):
    ctx, e, j, st = labels
    assert _render(ctx, text) == expected.format(e=e, j=j, st=st)
