"""判決索引規則二：從法院理由抓出「引用見解」的判決字號（計畫 3 丙 Task 2）。寧可漏，不可錯。"""
from app.corpus.case_refs import extract_case_refs, judgment_key, make_key

OWN = "臺灣臺中地方法院"
def keys(text, own_key=None): return [h.key for h in extract_case_refs(text, OWN, own_key)]

def test_parenthesised_authority_citation():
    t = "核定相當之數額（最高法院51年度台上字第223號判決意旨參照）。"
    assert keys(t) == ["最高法院|51|台上|223"]
    assert extract_case_refs(t, OWN, None)[0].display == "最高法院51年度台上字第223號"

def test_precedent_without_du_matches_same_key():
    assert keys("（最高法院51年台上字第223號判例參照）") == ["最高法院|51|台上|223"]

def test_list_in_one_parenthesis_shares_court():
    assert keys("（最高法院103年度台上字第235號裁定、105年度台上字第686號判決意旨參照）") == [
        "最高法院|103|台上|235", "最高法院|105|台上|686"]

def test_same_court_means_previous_court():
    assert keys("（最高法院97年度台上字第1號、同院98年度台上字第2號判決意旨參照）") == [
        "最高法院|97|台上|1", "最高法院|98|台上|2"]

def test_same_court_non_supreme():
    assert keys("（臺灣高等法院100年度上易字第1號、同院101年度上易字第2號判決意旨參照）") == [
        "台灣高等法院|100|上易|1", "台灣高等法院|101|上易|2"]

def test_court_carry_over_only_within_series():
    assert keys("（最高法院100年度台上字第1號、臺中高分院101年度上字第2號判決意旨參照）") == [
        "最高法院|100|台上|1", "不明|101|上|2"]

def test_own_court_with_yu():
    assert keys("（本院於110年度訴字第5517號民事判決五之(二)2.之見解）") == ["台灣台中地方法院|110|訴|5517"]

def test_follow_and_lead_markers():
    assert keys("最高法院55年度台上字第2053號民事判決闡述：") == ["最高法院|55|台上|2053"]
    assert keys("另參本院112年度訴字第631號") == ["台灣台中地方法院|112|訴|631"]
    assert keys("（如：85年度台上字第2975號判決）") == ["最高法院|85|台上|2975"]
    assert keys("另參本院112年度訴字第631號、113年度訴字第1415號") == [
        "台灣台中地方法院|112|訴|631", "台灣台中地方法院|113|訴|1415"]

def test_criminal_follow_marker_is_not_citation():
    assert keys("本院113年度交易字第1號刑事判決指出被告未注意車前狀況") == []

def test_mentions_are_not_citations():
    assert keys("業據援引本院114年度交簡字第110號刑事判決之認定為證") == []
    assert keys("（見本院112年度竹東簡字第235號卷第211頁）") == []
    assert keys("原告於本院113年度訴字第151號刑事案件準備程序時") == []
    assert keys("本院113年度交簡字第1號刑事判決（最高法院90年度台上字第1號判決意旨參照）") == ["最高法院|90|台上|1"]
    assert keys("（臺灣臺中地方檢察署113年度偵字第46057號起訴書參照）") == []

def test_nested_item_parentheses():
    t = "（另參最高法院110年度台上字第5517號民事判決五之(二)2.之見解）"
    assert keys(t) == ["最高法院|110|台上|5517"]

def test_criminal_citation_is_flagged():
    [h] = extract_case_refs("（最高法院76年度台上字第192號刑事判決意旨參照）", OWN, None)
    assert h.key == "最高法院|76|台上|192|刑" and h.display.endswith("（刑事）")

def test_own_number_and_unknown_court():
    assert keys("（本院113年度簡上字第211號判決參照）", own_key="台灣台中地方法院|113|簡上|211") == []
    assert keys("（憲法法庭111年憲判字第2號判決意旨參照）") == ["不明|111|憲判|2"]

def test_judgment_key():
    assert judgment_key("臺灣臺中地方法院113年度簡上字第211號民事判決") == "台灣台中地方法院|113|簡上|211"
    assert judgment_key("臺灣高等法院臺中分院113年度上字第5號民事判決") == "台灣高等法院台中分院|113|上|5"
    assert make_key("最高法院", 51, "臺上", 223) == "最高法院|51|台上|223"
