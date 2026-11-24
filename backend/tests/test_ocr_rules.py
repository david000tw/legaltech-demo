"""計畫 2A Task 4：規則檢查（看不清楚的字、不存在的日期、加總對不上）與提醒彙整。"""
from app.ocr.alerts import alert_color, compute_alerts
from app.ocr.rules import rule_alerts

RECEIPT = ("仁和綜合醫院醫療費用收據\n"
           "病歷號碼：A1047788  收據編號：114-0326-0158\n"
           "就診日期：民國114年3月26日  科別：骨科\n"
           "項目  金額（新臺幣）\n"
           "掛號費  150元\n部分負擔  420元\nX光檢查（自費）  1,250元\n石膏固定材料費  3,860元\n診斷證明書工本費  100元\n"
           "合計  5,870元\n"
           "【印章】仁和醫院 收費專用章")
ESTIMATE = ("順達汽車保養廠估價單\n車號：ABC-5678  車型：TOYOTA ALTIS  估價日期：114年3月28日\n"
            "項次  項目  數量  單價  金額\n"
            "1  前保險桿  1  12,800  12,800\n2  左前大燈總成  1  18,500  18,500\n3  左前葉子板  1  9,600  9,600\n"
            "4  水箱護罩  1  3,450  3,450\n5  鈑金工資  1  24,000  24,000\n6  烤漆工資  3  7,500  22,500\n"
            "7  拆裝工資  1  6,800  6,800\n"
            "小計  97,650\n營業稅（5%）  4,883\n合計  102,533")


def kinds(alerts):
    return [a["kind"] for a in alerts]


def test_rules_unreadable_merged():
    text = "新臺幣1〔?〕〔?〕8,000元，另〔?〕"
    alerts = rule_alerts(text)
    assert kinds(alerts) == ["unreadable", "unreadable"]
    assert text[alerts[0]["start"]:alerts[0]["end"]] == "〔?〕〔?〕"
    assert alerts[0]["message"] == "有看不清楚的字"


def test_rules_bad_date_roc_and_western():
    text = "事故日：民國114年2月30日；付款：2024/2/30。"
    alerts = rule_alerts(text)
    assert kinds(alerts) == ["bad_date", "bad_date"]
    assert [text[a["start"]:a["end"]] for a in alerts] == ["民國114年2月30日", "2024/2/30"]
    assert alerts[0]["message"] == "日期不存在：民國114年2月30日"
    ok = "114年2月28日、2024年2月29日、民國113年2月29日、電話 02-2345-6789"
    assert rule_alerts(ok) == []


def test_rules_sum_mismatch_and_chain():
    alerts = rule_alerts(RECEIPT)
    assert kinds(alerts) == ["sum_mismatch"]
    a = alerts[0]
    assert a["message"] == "加總對不上（原文可能有誤）：明細合計 5,780，文件寫 5,870"
    assert RECEIPT[a["start"]:a["end"]] == "5,870元"
    assert rule_alerts(ESTIMATE) == []


def test_rules_ignore_checkboxes():
    text = "肇事因素：□未注意車前狀況 ■未依規定減速 ☑ 酒駕 ☐ 其他"
    assert rule_alerts(text) == []


def test_rules_numbers_keep_whitespace():
    from app.ocr.rules import line_amount
    assert line_amount("1  12,800  12,800") == ("12,800", 12800)
    assert line_amount("掛號費  150元") == ("150元", 150)
    assert line_amount("備註：右腕骨折") is None


def test_compute_alerts_blank_and_missing_proofreader():
    assert kinds(compute_alerts("  \n ", None, False)) == ["blank"]
    assert compute_alerts("", None, True)[0]["message"] == "空白頁？請確認"
    alerts = compute_alerts("新臺幣1〔?〕8,000元", None, False)
    assert kinds(alerts) == ["unreadable", "proofreader_missing"]
    assert alerts[-1]["message"] == "校對員未執行"
    with_proof = compute_alerts("共計新臺幣5,870元", [{"text": "共計新臺弊5,870元", "score": 1, "box": [0, 0, 9, 9]}], True)
    assert kinds(with_proof) == ["mismatch"]
    assert {alert_color(k) for k in ("unreadable", "bad_date", "sum_mismatch")} == {"red"}
    assert {alert_color(k) for k in ("mismatch", "proofreader_missing", "blank")} == {"yellow"}
    assert set(alerts[0]) == {"kind", "start", "end", "message", "proof_text", "box"}
