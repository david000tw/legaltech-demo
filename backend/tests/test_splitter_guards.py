"""切段器的危險方向：當事人主張絕不能被歸成法院判斷（否則 AI 會把當事人主張當法院見解引用）。

反例來自 Task 1～5 的 critic 審查：法院判斷區外以「按」開頭的程序段、甲乙層級、
「壹、原告主張」底下以「按」開頭的子項、內文含「經查封」等。
"""
import pytest

from app.corpus.splitter import split_judgment
from app.text_norm import normalize_text

PLAINTIFF = "被告駕車未注意車前狀況撞傷原告"
DEFENDANT = "原告自己超速，應負主要過失責任"
COURT = "原告受有傷害，有診斷證明書可稽"

CASES = {
    "procedural_an_before_claims": """主文
原告之訴駁回。
事實及理由
一、按訴狀送達後，原告不得將原訴變更或追加他訴，但擴張或減縮應受判決事項之聲明者，不在此限。
二、原告主張：被告駕車未注意車前狀況撞傷原告，致原告受有傷害，應賠償醫療費用10萬元。
三、被告則以：原告自己超速，應負主要過失責任，請求金額過高等語。
四、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
""",
    "procedural_an_one_party_hearing": """主文
原告之訴駁回。
事實及理由
一、按言詞辯論期日，當事人之一造不到場者，得依到場當事人之聲請，由其一造辯論而為判決。
二、原告主張：被告駕車未注意車前狀況撞傷原告，致原告受有傷害，應賠償醫療費用10萬元。
三、被告則以：原告自己超速，應負主要過失責任，請求金額過高等語。
四、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
""",
    "jia_yi_sections": """主文
原告之訴駁回。
事實及理由
甲、程序方面：
一、按訴狀送達後，原告不得將原訴變更或追加他訴，但擴張或減縮應受判決事項之聲明者，不在此限。
乙、實體方面：
一、原告主張：被告駕車未注意車前狀況撞傷原告，致原告受有傷害，應賠償醫療費用10萬元。
二、被告則以：原告自己超速，應負主要過失責任，請求金額過高等語。
三、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
""",
    "level1_party_with_an_children": """主文
原告之訴駁回。
事實及理由
壹、原告主張：
一、按因故意或過失，不法侵害他人之權利者，負損害賠償責任。被告駕車未注意車前狀況撞傷原告，應負賠償責任。
二、原告因此支出醫療費用10萬元，並受有精神上痛苦，請求慰撫金50萬元。
貳、被告則以：
一、按損害之發生或擴大，被害人與有過失者，法院得減輕賠償金額。原告自己超速，應負主要過失責任。
二、原告請求之慰撫金顯然過高。
參、本院之判斷：
一、經查原告受有傷害，有診斷證明書可稽。
""",
    "jingchafeng_in_plaintiff_heading": """主文
原告之訴駁回。
事實及理由
一、原告主張：系爭房地經查封拍賣，被告駕車未注意車前狀況撞傷原告一事亦經判決確定。
二、被告則以：原告自己超速，應負主要過失責任，原告之請求無理由等語。
三、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
""",
    "jingchafeng_in_defendant_heading": """主文
原告之訴駁回。
事實及理由
一、原告主張：被告駕車未注意車前狀況撞傷原告，應賠償醫療費用。
二、被告則以：系爭車輛經查封後由第三人使用，且原告自己超速，應負主要過失責任等語。
三、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
""",
}


def _kinds(passages, needle):
    n = normalize_text(needle)
    return {k for k, t in passages if n in normalize_text(t)}


@pytest.mark.parametrize("name", list(CASES))
def test_party_claims_never_become_court_reasoning(name):
    passages = split_judgment(CASES[name])
    assert "court_reasoning" not in _kinds(passages, PLAINTIFF), name
    assert "court_reasoning" not in _kinds(passages, DEFENDANT), name
    assert _kinds(passages, COURT) == {"court_reasoning"}, name


def test_level1_court_section_children_stay_court():
    """「參、本院判斷」底下的「一、按…」「三、綜上…」仍是法院判斷；子項的訴訟費用仍是 other。"""
    text = """事實及理由
壹、原告主張：被告應返還借款。
貳、被告均稱：同意原告請求等語。
參、本院判斷：
一、按各共有人，得隨時請求分割共有物。
二、綜上，原告請求為有理由。
三、訴訟費用負擔之依據：民事訴訟法第78條。
"""
    passages = split_judgment(text)
    assert _kinds(passages, "按各共有人，得隨時請求分割共有物") == {"court_reasoning"}
    assert _kinds(passages, "綜上，原告請求為有理由") == {"court_reasoning"}
    assert _kinds(passages, "訴訟費用負擔之依據") == {"other"}
    assert _kinds(passages, "同意原告請求") == {"party_claim"}


def test_lue_yi_heading_is_party_claim():
    """「原告略以：」「被告略以：」是當事人主張的標題，和「略謂」一樣不能被當成法院判斷。"""
    text = """主文
原告之訴駁回。
事實及理由
一、原告略以：被告駕車未注意車前狀況撞傷原告，致原告受有傷害，應賠償醫療費用10萬元。
二、被告略以：原告自己超速，應負主要過失責任，請求金額過高等語。
三、本院之判斷：
㈠經查原告受有傷害，有診斷證明書可稽。
"""
    passages = split_judgment(text)
    assert _kinds(passages, PLAINTIFF) == {"party_claim"}
    assert _kinds(passages, DEFENDANT) == {"party_claim"}
    assert _kinds(passages, COURT) == {"court_reasoning"}
