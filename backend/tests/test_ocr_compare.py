"""計畫 2A Task 4：AI 與校對員（PaddleOCR）比對。資料取自比較測試的實際輸出。"""
from app.ocr.compare import compare


def cell(text, x1, y1, x2=None, y2=None, score=0.99):
    return {"text": text, "score": score, "box": [x1, y1, x2 if x2 is not None else x1 + 20 * len(text),
                                                   y2 if y2 is not None else y1 + 30]}


def test_compare_flags_silent_correction():
    ai = "醫療費用共計新臺幣5,870元整"
    c = cell("醫療費用共計新臺弊5,870元整", 100, 200)
    (a,) = compare(ai, [c])
    assert a["kind"] == "mismatch"
    assert ai[a["start"]:a["end"]] == "幣"
    assert a["proof_text"] == "弊" and a["box"] == c["box"]
    assert a["message"] == "校對員讀成「弊」，請對照原圖"


def test_compare_flags_blot_digit():
    ai = "另車輛修復費用新臺幣1〔?〕8,000元"
    alerts = compare(ai, [cell("另車輛修復費用新臺幣108,000元", 50, 50)])
    lo, hi = ai.index("1〔"), ai.index("8,000") + 1
    assert len(alerts) == 1 and lo <= alerts[0]["start"] <= alerts[0]["end"] <= hi


def test_compare_ignores_punctuation_and_spaces():
    ai = "姓名：林大明  身分證字號：H12****789"
    assert compare(ai, [cell("姓名林大明身分證字號H12****789", 10, 10)]) == []
    assert compare("備註：右腕骨折，宜休養四週。", [cell("備註:右腕骨折,宜休養四週", 10, 10)]) == []


def test_compare_ignores_stamp_lines():
    ai = "仁和綜合醫院醫療費用收據\n合計  5,780元\n【印章】仁和醫院 收費專用章"
    cells = [cell("仁和綜合醫院醫療費用收據", 100, 50), cell("仁和醫院", 300, 80), cell("合計", 100, 120),
             cell("收費專用章", 300, 150), cell("5,780元", 400, 120)]
    assert compare(ai, cells) == []


def test_compare_groups_cells_into_lines():
    ai = "掛號費  150元\n部分負擔  420元"
    cells = [cell("150元", 400, 96), cell("掛號費", 100, 100), cell("420元", 400, 146), cell("部分負擔", 100, 150)]
    assert compare(ai, cells) == []


def test_compare_reports_dropped_digit():
    ai = "1  前保險桿  1  12,800"
    alerts = compare(ai, [cell("1", 50, 300), cell("前保險桿", 100, 300), cell("12,800", 400, 300)])
    assert len(alerts) == 1
    a = alerts[0]
    assert ai[a["start"]:a["end"]] == "1"
    assert a["proof_text"] == ""


def test_compare_ignores_moved_block():
    ai = "項目  金額\nX光檢查（自費）  1,250元"
    cells = [cell("X光檢查（自費）", 100, 100), cell("1,250元", 400, 100), cell("項目", 100, 50), cell("金額", 400, 50)]
    cells[0]["box"][1] = 30  # 校對員把這一行讀到最上面
    cells[1]["box"][1] = 30
    cells[0]["box"][3] = 60
    cells[1]["box"][3] = 60
    cells[2]["box"][1], cells[2]["box"][3] = 100, 130
    cells[3]["box"][1], cells[3]["box"][3] = 100, 130
    assert compare(ai, cells) == []


def test_compare_merges_nearby_differences():
    ai = "就診日期：民國114年3月26日"
    (a,) = compare(ai, [cell("就診日期民國114年8月28日", 10, 10)])
    assert ai[a["start"]:a["end"]].startswith("3") and ai[a["start"]:a["end"]].endswith("6")
    assert a["proof_text"] == "8月28"


def _covers(ai, alerts, part):
    lo = ai.index(part)
    hi = lo + len(part)
    return any(a["start"] < hi and a["end"] > lo for a in alerts) or any(
        a["start"] == a["end"] and lo <= a["start"] <= hi for a in alerts)


def test_compare_flags_dropped_zeros_even_if_seen_elsewhere():
    # AI 少讀兩個 0（100 倍）；「00」在同一頁別處也有，不能當成「換了位置」
    ai = "掛號費  100元\n車損修復費用  1,000元"
    cells = [cell("掛號費", 100, 100), cell("100元", 400, 100),
             cell("車損修復費用", 100, 150), cell("100,000元", 400, 150)]
    alerts = compare(ai, cells)
    assert alerts and _covers(ai, alerts, "1,000元")
    assert any("00" in a["proof_text"] for a in alerts)


def test_compare_flags_two_digit_swap_in_table():
    # 「28」「73」在表格別列也有：一串數字中間換掉的字仍要提醒
    ai = "前保險桿  12,800\n工資  2,873"
    cells = [cell("前保險桿", 100, 100), cell("17,300", 400, 100), cell("工資", 100, 150), cell("2,873", 400, 150)]
    alerts = compare(ai, cells)
    assert alerts and _covers(ai, alerts, "12,800")


def test_compare_ignores_moved_whole_number_row():
    # 整列（含完整金額）換了位置：仍不算不一致
    ai = "掛號費  150元\n部分負擔  420元"
    cells = [cell("部分負擔", 100, 50), cell("420元", 400, 50), cell("掛號費", 100, 100), cell("150元", 400, 100)]
    assert compare(ai, cells) == []
