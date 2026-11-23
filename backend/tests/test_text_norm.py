from app.text_norm import find_quote, normalize_text, quote_length


def test_quote_across_newline():
    hay = "未在適當位置暫停禮讓\n幹線道之系爭車輛"
    q = "暫停禮讓幹線道"
    span = find_quote(hay, q)
    assert span is not None
    s, e = span
    assert normalize_text(hay[s:e]) == normalize_text(q)


def test_fullwidth_digits_match():
    hay = "原告請求慰撫金１５０，０００元，實屬過高"
    span = find_quote(hay, "150,000元")
    assert span is not None
    s, e = span
    assert hay[s:e] == "１５０，０００元"


def test_not_found_returns_none():
    assert find_quote("被告未注意車前狀況", "原告超速行駛") is None
    assert find_quote("被告未注意車前狀況", "   ") is None


def test_quote_length_ignores_spaces():
    assert quote_length("違反 保護他人\n之法律") == 9


def test_normalize_removes_nbsp_and_ideographic_space():
    assert normalize_text("原\xa0 \xa0 告　楊家豪") == "原告楊家豪"
