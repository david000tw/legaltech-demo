"""計畫 2B：引用它的那句話與指紋（設計 7.2）。"""
import pytest

from app.db import tenant_session
from app.models import Paragraph
from app.support.fingerprint import (
    citing_sentences, neutral_sentence, split_sentences, support_fingerprint, support_inputs,
)
from tests.verify_helpers import (
    E_QUOTE, EVIDENCE_TEXT, J_REASONING, make_paragraph, setup_verify_round,
)

pytestmark = pytest.mark.support_real


def test_split_sentences_merges_trailing_marker():
    assert split_sentences("被告闖紅燈。此有收據可稽。（{{cite:E1}}）\n原告受傷。") == [
        "被告闖紅燈。", "此有收據可稽。（{{cite:E1}}）", "原告受傷。"]


def test_split_sentences_keeps_closing_quote_with_sentence():
    assert split_sentences("原告稱「被告闖紅燈。」此有{{cite:E1}}可稽。") == [
        "原告稱「被告闖紅燈。」", "此有{{cite:E1}}可稽。"]


def test_citing_sentences_marker_after_period_and_two_sentences():
    text = "按{{cite:J1}}認{{quote:J1}}。被告應賠償。另參{{cite:J1}}意旨。"
    assert citing_sentences(text, "J1") == ["按{{cite:J1}}認{{quote:J1}}。", "另參{{cite:J1}}意旨。"]
    assert citing_sentences("醫療費用5,780元。{{cite:E1}}", "E1") == ["醫療費用5,780元。{{cite:E1}}"]


def test_marker_after_period_followed_by_more_text():
    # 律師把出處放在句號後面、後面還有別句：出處要跟前一句（主張所在的句子）在一起
    assert citing_sentences("原告支出醫療費5,780元。（見{{cite:E1}}）被告自應賠償。", "E1") == [
        "原告支出醫療費5,780元。（見{{cite:E1}}）"]
    assert citing_sentences("原告支出醫療費5,780元。（見{{cite:E1}}）\n被告自應賠償。", "E1") == [
        "原告支出醫療費5,780元。（見{{cite:E1}}）"]
    assert citing_sentences("原告支出5,780元。（參{{cite:E1}}、{{cite:E2}}第3頁）另查無誤。", "E2") == [
        "原告支出5,780元。（參{{cite:E1}}、{{cite:E2}}第3頁）"]
    # 標記緊接在上一句結尾之後、作為下一句開頭：連同上一句一起看（寧可多看，不要漏看主張）
    assert citing_sentences("按民法規定「應負責任。」{{cite:S1}}本件被告過失。", "S1") == [
        "按民法規定「應負責任。」", "{{cite:S1}}本件被告過失。"]


def test_claim_before_trailing_marker_is_in_fingerprint():
    q = {"E1": "合計新臺幣5,780元"}
    a = support_fingerprint("evidence", "收據", q["E1"],
                            citing_sentences("原告支出醫療費5,780元。（見{{cite:E1}}）被告自應賠償。", "E1"), "E1", q)
    b = support_fingerprint("evidence", "收據", q["E1"],
                            citing_sentences("原告支出醫療費57,800元。（見{{cite:E1}}）被告自應賠償。", "E1"), "E1", q)
    assert a != b


def test_neutral_sentence():
    q = {"E1": "合計5,780元", "E2": "支出醫療費"}
    assert neutral_sentence("依{{cite:E2}}及{{cite:E1}}，{{quote:E2}}{{quote:E1}}。", "E1", q) == \
        "依〔引用〕及〔本引用〕，「支出醫療費」〔本引文〕。"
    assert neutral_sentence("另{{quote:E9}}。", "E1", q) == "另〔引文〕。"


def test_fingerprint_stability():
    # 同段改別句、證物改號（代碼由 E1 變 E2）→ 指紋不變；改引用它的句子、或來源文字改了 → 指紋改變
    src, q = "醫療費用合計新臺幣5,780元", "醫療費用合計新臺幣5,780元"
    a = support_fingerprint("evidence", src, q, ["此有{{cite:E1}}可稽。"], "E1", {"E1": q})
    assert a == support_fingerprint("evidence", src, q, ["此有{{cite:E2}}可稽。"], "E2", {"E2": q})
    assert a == support_fingerprint("evidence", src + " ", q, ["此有{{cite:E1}}可稽。 "], "E1", {"E1": q})  # 空白不算
    assert a != support_fingerprint("evidence", src, q, ["此有{{cite:E1}}足證。"], "E1", {"E1": q})
    assert a != support_fingerprint("evidence", src + "整", q, ["此有{{cite:E1}}可稽。"], "E1", {"E1": q})
    assert len(a) == 64


def test_fingerprint_follows_other_quotes():
    # 同一句裡 E2 的引文由 5,780元 改成 57,800元（仍逐字出自 E2）→ E1 的指紋要變
    s = ["原告支出{{quote:E2}}，此有{{cite:E1}}可稽。"]
    a = support_fingerprint("evidence", "收據", "收據", s, "E1", {"E1": "收據", "E2": "5,780元"})
    assert a != support_fingerprint("evidence", "收據", "收據", s, "E1", {"E1": "收據", "E2": "57,800元"})
    # E2 改號成 E3、引文相同 → 不變
    s3 = ["原告支出{{quote:E3}}，此有{{cite:E1}}可稽。"]
    assert a == support_fingerprint("evidence", "收據", "收據", s3, "E1", {"E1": "收據", "E3": "5,780元"})


def test_support_inputs_for_paragraph(db):
    ctx = setup_verify_round()
    # make_paragraph 不重新驗證，引用的 verify_status 仍是 failed；support_inputs 照樣要算
    pid = make_paragraph(ctx, "按{{cite:J1}}認被告{{quote:J1}}，此有{{cite:E1}}可稽。",
                         [("J1", J_REASONING), ("E1", E_QUOTE)])
    with tenant_session(ctx["firm"]) as s:
        got = support_inputs(s, s.get(Paragraph, pid))
    assert set(got) == {"J1", "E1"}
    assert got["E1"].kind == "evidence" and got["E1"].source == EVIDENCE_TEXT
    assert "【本引用：" in got["E1"].sentence and "「" + J_REASONING + "」" in got["E1"].sentence
    assert got["J1"].fingerprint != got["E1"].fingerprint


def test_support_inputs_skips_statement_and_uncited(db):
    ctx = setup_verify_round()
    pid = make_paragraph(ctx, "被告應賠償。", [("E1", E_QUOTE)])  # 引用沒有出現在文字裡
    with tenant_session(ctx["firm"]) as s:
        p = s.get(Paragraph, pid)
        assert support_inputs(s, p) == {}
        p.text, p.kind = "此有{{cite:E1}}可稽。", "statement"
        assert support_inputs(s, p) == {}
