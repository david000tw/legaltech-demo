"""引用標記的顯示：AI 用括號包起來當出處的引用「（{{cite:X}}）」，系統補上「意旨參照」「參照」；
其他寫法一律照原樣（不猜文法：審查員用 828 段實際草稿驗證過，靠前後字猜「是不是句尾出處」會把
「被告違反（…參照），」這種句子弄壞）。"""
import pytest

from app.db import tenant_session
from app.models import Paragraph
from app.render import render_text
from tests.verify_helpers import E_QUOTE, J_REASONING, make_paragraph, setup_verify_round

CITES = [("J1", J_REASONING), ("E1", E_QUOTE), ("S1", "")]


@pytest.fixture
def labels(db):
    ctx = setup_verify_round()
    pid = make_paragraph(ctx, "依{{cite:E1}}、依{{cite:J1}}、依{{cite:S1}}規定", CITES, seq=9)
    with tenant_session(ctx["firm"]) as s:
        e, j, st = render_text(s, s.get(Paragraph, pid)).removesuffix("規定").split("、")
    return ctx, e.removeprefix("依"), j.removeprefix("依"), st.removeprefix("依")


def _render(ctx, text, seq=1):
    pid = make_paragraph(ctx, text, CITES, seq=seq)
    with tenant_session(ctx["firm"]) as s:
        return render_text(s, s.get(Paragraph, pid))


@pytest.mark.parametrize("text,expected", [
    ("被告未讓幹道車先行（{{cite:E1}}）。", "被告未讓幹道車先行（{e}）。"),
    ("被告應負過失責任（{{cite:J1}}）；", "被告應負過失責任（{j}意旨參照）；"),
    ("駕駛人應減速慢行（{{cite:S1}}），", "駕駛人應減速慢行（{st}參照），"),
    ("被告應負責（{{cite:J1}}參照）。", "被告應負責（{j}意旨參照）。"),  # AI 自己寫了參照：補意旨，不重複
    ("被告應負責（{{cite:S1}}參照）。", "被告應負責（{st}參照）。"),
    ("被告應負責（{{cite:J1}}意旨參照）。", "被告應負責（{j}意旨參照）。"),
    ("被告應負責（{{cite:S1}}{{cite:J1}}）。", "被告應負責（{st}、{j}意旨參照）。"),
    ("被告有過失（{{cite:E1}}{{cite:J1}}）。", "被告有過失（{e}；{j}意旨參照）。"),
    ("研判表載明{{quote:E1}}（{{cite:E1}}）。", "研判表載明「" + E_QUOTE + "」（{e}）。"),
])
def test_parenthesized_references_are_completed(labels, text, expected):
    ctx, e, j, st = labels
    assert _render(ctx, text) == expected.format(e=e, j=j, st=st)


@pytest.mark.parametrize("text,expected", [
    # 句中當文字用的引用：照原樣（審查員從實際草稿找到的句型）
    ("被告違反{{cite:S1}}，應負過失責任。", "被告違反{st}，應負過失責任。"),
    ("原告援引{{cite:S1}}，主張被告應賠償。", "原告援引{st}，主張被告應賠償。"),
    ("本件應類推適用{{cite:S1}}，", "本件應類推適用{st}，"),
    ("依{{cite:S1}}規定，被告應負責。", "依{st}規定，被告應負責。"),
    ("按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。", "按{j}認被告「" + J_REASONING + "」，此有{e}可稽。"),
    ("{{cite:S1}}定有明文。", "{st}定有明文。"),
    # 沒加括號的句尾出處：不猜，照原樣（提示詞要求 AI 寫成括號形式）
    ("始足當之{{cite:J1}}。", "始足當之{j}。"),
    ("被告有過失{{cite:E1}}、{{cite:E1}}。", "被告有過失{e}、{e}。"),
    # 括號裡還有別的字：照原樣，不會變成雙層括號
    ("（同上{{cite:E1}}）", "（同上{e}）"),
])
def test_other_citations_stay_as_written(labels, text, expected):
    ctx, e, j, st = labels
    assert _render(ctx, text) == expected.format(e=e, j=j, st=st)
