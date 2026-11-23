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


def test_zero_width_and_soft_hyphen_ignored():
    hay = "被告未注意​車前狀況且未­減速"
    span = find_quote(hay, "被告未注意車前狀況且未減速")
    assert span == (0, len(hay))


def test_partial_expanded_char_not_matched():
    # ㈠ 經 NFKC 會展開成 (一)；引文只對到展開後的一部分時不算逐字。
    assert find_quote("㈠原告主張", "一)原告主張") is None
    assert find_quote("㈠原告主張", "原告主張") == (1, 5)
    assert find_quote("㈠原告主張", "(一)原告主張") == (0, 5)


def test_span_excludes_leading_and_trailing_whitespace():
    hay = "原告\n  主張：被告未減速。  \n下一段"
    s, e = find_quote(hay, "主張：被告未減速。")
    assert hay[s:e] == "主張：被告未減速。"
