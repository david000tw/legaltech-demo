"""驗收測試自己的金額核對（不呼叫 AI）：確認它讀得懂各種金額寫法，而且判決引文只放行引號內的那段。"""
from tests.e2e.test_e2e_real import _independent_amount_check, _simple_amounts, _unparsed_amounts

# 種子判決 TCDV_113_jianshang_211 裡逐字存在的一段（含金額 20 萬元）
REAL_QUOTE = "其肉體及精神上受有相當痛苦，而請求賠償精神慰撫金20萬元"
FAKE_QUOTE = "認原告請求精神慰撫金以333,333元為適當"


def _judgment(quote: str) -> dict:
    return {"kind": "judgment", "quote": quote}


def test_oracle_reads_amount_forms():
    assert _simple_amounts("醫療費用合計：新臺幣 45,000 元。") == {45000}
    assert _simple_amounts("特留分約為新臺幣28萬1,894元") == {281894}
    assert _simple_amounts("每月2萬5千元") == {25000}
    assert _simple_amounts("新臺幣參萬元") == {30000}
    assert _simple_amounts("一百萬元") == {1000000}
    assert _simple_amounts("二百四十萬元") == {2400000}
    assert _simple_amounts("1.5萬元") == {15000}
    assert _simple_amounts("1億2,000萬元") == {120000000}
    assert _simple_amounts("原證1\n2,000元") == {2000}  # 換行前的數字不能黏到金額上


def test_oracle_lists_amounts_it_cannot_read():
    assert _unparsed_amounts("賠償十餘萬元，另支出數千元") == ["十餘萬元", "數千元"]
    assert _unparsed_amounts("新臺幣45,000元") == []
    assert _unparsed_amounts("其餘60萬元由原告取得") == []  # 「其餘」的餘不是「十餘萬」的餘


def test_oracle_allows_amounts_only_inside_verified_judgment_quotes():
    text = f"判決認「{REAL_QUOTE}」。"
    assert _independent_amount_check("demo-lin", {}, {"exported_text": text},
                                     [{"citations": [_judgment(REAL_QUOTE)]}]) == []


def test_oracle_flags_same_amount_written_outside_the_quote():
    text = f"判決認「{REAL_QUOTE}」。原告亦請求精神慰撫金新臺幣20萬元。"
    assert _independent_amount_check("demo-lin", {}, {"exported_text": text},
                                     [{"citations": [_judgment(REAL_QUOTE)]}]) == [200000]


def test_oracle_ignores_quote_not_found_in_judgments():
    text = f"判決認「{FAKE_QUOTE}」。"
    assert _independent_amount_check("demo-lin", {}, {"exported_text": text},
                                     [{"citations": [_judgment(FAKE_QUOTE)]}]) == [333333]


def test_oracle_ignores_quote_only_cited_by_marker():
    # 引文本身沒出現在匯出文字裡（只有標記），裡面的金額不能拿來放行正文
    text = "原告請求精神慰撫金新臺幣20萬元。"
    assert _independent_amount_check("demo-lin", {}, {"exported_text": text},
                                     [{"citations": [_judgment(REAL_QUOTE)]}]) == [200000]


def test_oracle_checks_drafts_that_could_not_export():
    paragraphs = [{"heading": "關於慰撫金", "rendered_text": "請求新臺幣33萬3,333元。", "citations": []}]
    assert _independent_amount_check("demo-lin", {}, {"exported_text": None}, paragraphs) == [333333]
