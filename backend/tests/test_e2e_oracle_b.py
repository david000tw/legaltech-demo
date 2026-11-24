"""子專案乙驗收的獨立核對（不呼叫 AI）：測試程式自己判斷摘錄在不在頁上、日期金額在不在摘錄裡，
不借用產品的 app.sources、app.dates、app.amounts，避免產品錯了核對也跟著錯。"""
import copy

import pytest

from tests.e2e.test_e2e_real import _assert_report_b, _own_dates, _own_found, _own_month_days

PAGE = "收據\n民國114年3月9日 林大明 支付醫療費用新臺幣45,000元。"
OPP = "被告答辯：民國114年3月9日下午，原告闖紅燈在先。"


def test_own_found_requires_six_chars_and_page():
    assert _own_found(PAGE, "林大明 支付醫療費用")
    assert _own_found(PAGE, "林大明支付醫療費用")  # 空白不算
    assert _own_found("ＡＢＣ醫療費用支出", "ABC醫療費用")  # 全形半形視為相同
    assert not _own_found(PAGE, "醫療費用")  # 不足 6 字
    assert not _own_found(PAGE, "林大明支付掛號費用")
    assert not _own_found(None, "林大明 支付醫療費用")  # 掃描頁


@pytest.mark.parametrize("text,expected", [
    ("民國114年3月9日", {(2025, 3, 9)}), ("114年3月9日", {(2025, 3, 9)}), ("民國一一四年三月九日", {(2025, 3, 9)}),
    ("一百一十四年三月九日", {(2025, 3, 9)}), ("2025年3月9日", {(2025, 3, 9)}), ("2025/03/09", {(2025, 3, 9)}),
    ("114.3.9", {(2025, 3, 9)}), ("民國114年3月", {(2025, 3, None)}), ("一一○年十月一日", {(2021, 10, 1)}),
    ("114年元月5日", {(2025, 1, 5)}), ("第184條", set()), ("114年度訴字第1287號", set()),
])
def test_own_dates_reads_roc_ad_and_chinese(text, expected):
    assert _own_dates(text) == expected


def _report():
    src = {"page_id": "p1", "label": "原證1 第1頁", "quote": "民國114年3月9日 林大明 支付醫療費用新臺幣45,000元",
           "status": "found", "reasons": []}
    return {"demo": {
        "brief_kind": "preparatory", "round_status": "drafted",
        "jobs": {k: {"status": "done", "error": None} for k in ("evidence_notes", "timeline", "health")},
        "pages": {"p1": {"kind": "evidence", "text": PAGE}, "p2": {"kind": "opponent", "text": OPP}},
        "notes": [{"exhibit": "原證1", "kind": "proves", "text": "原告於民國114年3月9日支付醫療費用45,000元",
                   "origin": "ai", "source": dict(src)}],  # 各自一份：改說明的出處不會連帶改到事件
        "events": [
            {"date": "2025-03-09", "description": "原告支付醫療費用", "amount": 45000, "source_kind": "evidence",
             "origin": "ai", "source": dict(src)},
            {"date": "2025-03-09", "description": "被告稱原告闖紅燈", "amount": None, "source_kind": "opponent",
             "origin": "ai", "source": {**src, "page_id": "p2", "quote": "民國114年3月9日下午，原告闖紅燈在先"}},
        ],
        "health": {"items": [{"level": "high", "title": "與有過失", "explanation": "對造主張原告闖紅燈",
                              "basis": [{**src, "page_id": "p2", "quote": "原告闖紅燈在先"}],
                              "references": [{"code": "J1", "label": "判決"}], "not_in_list": []}],
                   "undisputed": [{"fact": "事故日期", "basis": {**src, "page_id": "p2", "quote": "民國114年3月9日下午"}}],
                   "issues": ["過失比例"]},
        "candidate_codes": ["J1", "S1"],
    }}


def test_assert_b_accepts_good_report():
    _assert_report_b(_report())


def test_assert_b_flags_wrong_found():
    r = _report()
    r["demo"]["notes"][0]["source"]["quote"] = "這句話不在頁上但系統標了找到"
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r = _report()
    r["demo"]["events"][0]["amount"] = 46000  # 金額不在摘錄裡卻標 found
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r = _report()
    r["demo"]["events"][0]["date"] = "2025-03-10"
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r = _report()
    r["demo"]["events"][1]["source_kind"] = "evidence"  # 出處是對造書狀頁卻沒標對造主張
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r = _report()
    r["demo"]["health"]["undisputed"][0]["basis"]["page_id"] = "p1"  # 不爭執事項出自我方卷證
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r = _report()
    r["demo"]["health"]["items"][0]["references"] = [{"code": "J9", "label": "x"}]  # 不在候選
    with pytest.raises(AssertionError):
        _assert_report_b(r)


def test_assert_b_flags_forbidden_words():
    for path in (("notes", 0, "text"), ("events", 0, "description")):
        r = _report()
        r["demo"][path[0]][path[1]][path[2]] += "，已證實"
        with pytest.raises(AssertionError):
            _assert_report_b(r)
    r = _report()
    r["demo"]["health"]["issues"].append("責任已驗證")
    with pytest.raises(AssertionError):
        _assert_report_b(r)


def test_assert_b_requires_floor():
    r = _report()
    r["demo"]["notes"][0]["source"] = {**r["demo"]["notes"][0]["source"], "status": "not_found",
                                       "reasons": ["quote_not_found"]}
    with pytest.raises(AssertionError):
        _assert_report_b(r)  # 沒有任何 found 的卷證說明
    r = _report()
    for e in r["demo"]["events"]:
        e["date"] = None
    with pytest.raises(AssertionError):
        _assert_report_b(r)  # 沒有沒標紅且有日期的事件


def test_assert_b_complaint_has_no_undisputed_and_jobs_done():
    r = _report()
    r["demo"]["brief_kind"] = "complaint"
    with pytest.raises(AssertionError):
        _assert_report_b(r)
    r["demo"]["health"]["undisputed"] = None
    _assert_report_b(r)
    r2 = copy.deepcopy(r)
    r2["demo"]["jobs"]["timeline"] = {"status": "failed", "error": "timeout: x"}
    with pytest.raises(AssertionError):
        _assert_report_b(r2)


def test_own_month_days():
    assert _own_month_days("原告在9月25日已說先付清") == {(9, 25)}
    assert _own_month_days("陳志豪（09/18 19:42）：不好意思") == {(9, 18)}
    assert _own_month_days("十月二十六日才知道") == {(10, 26)}
    assert _own_month_days("民國115年9月30日付款，2026/09/19至2026/10/28") == set()
    assert _own_month_days("應繼分各1/4、3/10") == set()


def _raises(r):
    with pytest.raises(AssertionError):
        _assert_report_b(r)


# critic：每個核對分支各有一個會失敗的例子；拿掉任何一行核對，至少一個測試會失敗
BAD_TEXTS = ("原告於民國114年3月10日支付醫療費用", "原告支付醫療費用46,000元", "原告3月10日支付醫療費用")


def test_assert_b_flags_note_text_dates_and_amounts():
    for text in BAD_TEXTS:
        r = _report()
        r["demo"]["notes"][0]["text"] = text
        _raises(r)
    r = _report()
    r["demo"]["notes"][0]["text"] = "原告3月9日支付醫療費用45,000元"  # 沒寫年份，但月日在摘錄裡
    _assert_report_b(r)


def test_assert_b_flags_event_description_dates_and_amounts():
    for text in BAD_TEXTS:
        r = _report()
        r["demo"]["events"][0]["description"] = text
        _raises(r)


def test_assert_b_flags_health_and_undisputed_quote_not_on_page():
    r = _report()
    r["demo"]["health"]["items"][0]["basis"][0]["quote"] = "這句話不在對造書狀上面"
    _raises(r)
    r = _report()
    r["demo"]["health"]["undisputed"][0]["basis"]["quote"] = "這句話不在對造書狀上面"
    _raises(r)


def test_assert_b_flags_quote_not_on_page_alone():
    # 日期金額都在摘錄裡，只有摘錄不在頁上：只有「摘錄在頁上」那一關能抓到
    fake = "民國114年3月9日 林大明 支付掛號費用新臺幣45,000元"
    for where in ("notes", "events"):
        r = _report()
        r["demo"][where][0]["source"]["quote"] = fake
        _raises(r)


def test_assert_b_requires_health_report():
    r = _report()
    r["demo"]["health"] = None
    _raises(r)
