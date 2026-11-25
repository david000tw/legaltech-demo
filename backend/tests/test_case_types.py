"""案型登錄表、判決與法條的案型標籤、同類優先搜尋（設計 5.1、5.3）。"""
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.case_types import CASE_TYPES, procedure_for, procedure_warning
from app.corpus.search import search_articles, search_passages
from app.corpus.seed_loader import load_seed
from app.db import admin_session, tenant_session
from app.models import Case, Judgment, JudgmentPassage, Statute, StatuteArticle
from app.pipeline.case_context import case_context
from tests.helpers import create_firm

REASONING = "按被告明知原告之配偶已婚，仍與之交往並發生親密行為，已逾越社會一般通念所能容忍之範圍，侵害原告基於配偶關係之身分法益而情節重大。"


def test_registry_has_the_four_case_types():
    assert set(CASE_TYPES) == {"traffic_tort", "spousal_rights", "estate_partition", "nominee_registration"}
    assert CASE_TYPES["estate_partition"].procedure == "family"
    assert all(ct.issues for ct in CASE_TYPES.values())


def test_procedure_for():
    assert procedure_for("estate_partition", None) == "family"
    assert procedure_for("spousal_rights", "113年度家訴字第5號") == "family"
    assert procedure_for("estate_partition", "113年度重訴字第1號") == "civil"
    assert procedure_for("traffic_tort", "113 年度 中簡 字第 3946 號") == "civil"
    assert procedure_warning("spousal_rights", "113年度家訴字第5號") is not None
    assert procedure_warning("traffic_tort", "113年度中簡字第3946號") is None
    assert procedure_warning("estate_partition", None) is None


def _judgment(s, citation, text, case_types, curated=False):
    j = Judgment(citation=citation, court="測試法院", court_level="district", decided_on=date(2024, 1, 1),
                 source_url="https://example.invalid/", full_text=text, status="active", curated=curated,
                 case_types=case_types)
    s.add(j)
    s.flush()
    p = JudgmentPassage(judgment_id=j.id, seq=1, passage_type="court_reasoning", text=text, status="active")
    s.add(p)
    s.flush()
    return p.id


def test_search_boosts_same_type_without_excluding(db):
    with admin_session() as s:
        b = _judgment(s, "B判決", REASONING + "（車禍）", ["traffic_tort"])
        c = _judgment(s, "C判決", REASONING + "（無標籤）", [])
        a = _judgment(s, "A判決", REASONING + "（配偶權）", ["spousal_rights"])
        d = _judgment(s, "D判決", REASONING + "（兩個標籤）", ["spousal_rights", "traffic_tort"])
    with admin_session() as s:
        got = [p.id for p in search_passages(s, REASONING, 10, case_type="spousal_rights")]
        plain = [p.id for p in search_passages(s, REASONING, 10)]
    assert set(got[:2]) == {a, d}  # 同類加權排前面
    assert b in got and c in got  # 不排除其他類型與沒有標籤的
    assert len(got) == len(set(got)) == 4  # 兩個標籤的判決只出現一次
    assert set(plain) == {a, b, c, d}


def test_search_articles_boosts_same_type(db):
    with admin_session() as s:
        st = Statute(name="測試法", pcode=None)
        s.add(st)
        s.flush()
        s.add_all([StatuteArticle(statute_id=st.id, article_no="1", text=REASONING, case_types=["traffic_tort"]),
                   StatuteArticle(statute_id=st.id, article_no="2", text=REASONING + "。", case_types=["spousal_rights"])])
    with admin_session() as s:
        got = [a.article_no for a in search_articles(s, REASONING, 5, case_type="spousal_rights")]
    assert got == ["2", "1"]


def test_seed_loader_reads_and_updates_tags(db, tmp_path):
    (tmp_path / "judgments").mkdir()
    (tmp_path / "statutes").mkdir()
    j = {"citation": "測試判決", "court": "測試法院", "court_level": "district", "decided_on": "2024-01-01",
         "source_url": "https://example.invalid/", "curated": False, "raw_file": "x.pdf",
         "full_text": "事實及理由\n一、原告主張：被告應賠償。\n二、得心證之理由：" + REASONING,
         "case_types": ["spousal_rights"], "annotation": "少數見解：否定配偶權"}
    st = {"name": "測試法", "pcode": None,
          "articles": [{"article_no": "1", "text": REASONING, "effective_from": None, "case_types": ["spousal_rights"]}]}
    (tmp_path / "judgments" / "t.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "statutes" / "t.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    with admin_session() as s:
        load_seed(s, tmp_path)
    with admin_session() as s:
        got = s.scalars(select(Judgment).where(Judgment.citation == "測試判決")).one()
        assert got.case_types == ["spousal_rights"] and got.annotation == "少數見解：否定配偶權"
        art = s.scalars(select(StatuteArticle).where(StatuteArticle.article_no == "1")).one()
        assert art.case_types == ["spousal_rights"]
    j["case_types"], j["annotation"] = ["spousal_rights", "traffic_tort"], None
    st["articles"][0]["case_types"] = ["spousal_rights", "estate_partition"]
    (tmp_path / "judgments" / "t.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "statutes" / "t.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    with admin_session() as s:
        load_seed(s, tmp_path)
    with admin_session() as s:
        got = s.scalars(select(Judgment).where(Judgment.citation == "測試判決")).one()
        assert got.case_types == ["spousal_rights", "traffic_tort"] and got.annotation is None
        art = s.scalars(select(StatuteArticle).where(StatuteArticle.article_no == "1")).one()
        assert art.case_types == ["spousal_rights", "estate_partition"]


def test_case_context_lists_issues(db):
    firm = create_firm("甲事務所")
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="分割遺產", our_side="plaintiff", case_type="estate_partition")
        s.add(case)
        s.flush()
        text = case_context(s, case)
    assert "家事準備書狀" in text and "特留分" in text and "分割遺產" in text and "原告" in text


def test_create_case_rejects_unknown_type(lawyer_client):
    client, _, _ = lawyer_client
    assert client.post("/api/cases", json={"title": "x", "our_side": "plaintiff", "case_type": "divorce"}).status_code == 422
    r = client.post("/api/cases", json={"title": "x", "our_side": "plaintiff", "case_type": "estate_partition"})
    assert r.status_code == 200 and r.json()["case_type"] == "estate_partition"
    r = client.post("/api/cases", json={"title": "y", "our_side": "defendant"})
    assert r.json()["case_type"] == "traffic_tort"


def test_candidates_show_annotation_and_use_case_type(db, monkeypatch):
    from app.models import Candidate
    from app.pipeline import candidates as cand_mod
    from tests.verify_helpers import add_synthetic_court_passage, setup_verify_round
    ctx = setup_verify_round()
    pid = add_synthetic_court_passage(ctx, REASONING, "J8")
    with admin_session() as s:
        s.get(Judgment, s.get(JudgmentPassage, pid).judgment_id).annotation = "少數見解：否定配偶權"
    with tenant_session(ctx["firm"]) as s:
        c = s.scalars(select(Candidate).where(Candidate.round_id == ctx["round_id"], Candidate.code == "J8")).one()
        assert "少數見解：否定配偶權" in cand_mod.format_candidates(s, [c])
    seen = []
    monkeypatch.setattr(cand_mod, "search_passages", lambda s, q, k, **kw: seen.append(kw.get("case_type")) or [])
    monkeypatch.setattr(cand_mod, "search_articles", lambda s, q, k, **kw: seen.append(kw.get("case_type")) or [])
    from tests.pipeline_helpers import make_round
    firm = create_firm("乙事務所")
    case_id, round_id = make_round(firm)
    with tenant_session(firm) as s:
        s.get(Case, case_id).case_type = "nominee_registration"
        from app.models import Claim
        s.add(Claim(firm_id=firm, round_id=round_id, seq=1, summary="借名", quote="借名登記", quote_verified=True))
    with tenant_session(firm) as s:
        cand_mod.build_candidates(s, round_id)
    assert seen and set(seen) == {"nominee_registration"}


def test_annotation_reaches_citation_and_source(db):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.helpers import create_member, login
    from tests.verify_helpers import add_synthetic_court_passage, make_paragraph, setup_verify_round
    ctx = setup_verify_round()
    pid = add_synthetic_court_passage(ctx, REASONING, "J8")
    with admin_session() as s:
        s.get(Judgment, s.get(JudgmentPassage, pid).judgment_id).annotation = "少數見解：否定配偶權"
    make_paragraph(ctx, "按{{cite:J8}}認{{quote:J8}}。", [("J8", REASONING[:30])])
    create_member(ctx["firm"], "lawyer@a.test")
    c = TestClient(create_app(db))
    assert login(c, "lawyer@a.test").status_code == 200
    cite = c.get(f"/api/drafts/{ctx['draft_id']}").json()["paragraphs"][0]["citations"][0]
    assert cite["annotation"] == "少數見解：否定配偶權"
    assert c.get(f"/api/citations/{cite['id']}/source").json()["annotation"] == "少數見解：否定配偶權"



# ---- critic（Task 6～9）M3：案號不是完整格式也要看得出字別；完全看不出來就提醒 ----
@pytest.mark.parametrize("number", ["113年度家訴字5號", "113家訴5", "113年家訴第5號", "113 年度家訴字第 5 號",
                                    "一一三年度家訴字第五號"])
def test_procedure_reads_loose_case_numbers(number):
    assert procedure_for("spousal_rights", number) == "family"
    assert "家訴" in procedure_warning("spousal_rights", number)


def test_loose_civil_case_number_on_family_type():
    assert procedure_for("estate_partition", "113重訴1") == "civil"
    assert "重訴" in procedure_warning("estate_partition", "113重訴1")


def test_unreadable_case_number_warns():
    assert procedure_for("spousal_rights", "第5號") == "civil"  # 看不出字別：照案型預設
    assert "無法判讀" in procedure_warning("spousal_rights", "第5號")
    assert procedure_warning("spousal_rights", "  ") is None  # 沒填不算


@pytest.mark.parametrize("number", ["尚未分案", "待補", "（未定）", "N/A", "—", "?", "113-5", "待補113",
                                    "113年度○○字第○○號", "113年度　字第　號", "113年度XX字第XX號", "113年度待補字第5號"])
def test_placeholder_case_number_keeps_case_type_procedure(number):
    """律師常先填暫代文字：不能把遺產案翻成民事（書狀名稱、受文都會錯）。"""
    assert procedure_for("estate_partition", number) == "family"
    assert "無法判讀" in procedure_warning("estate_partition", number)


# ---- critic（Task 6～9）M4：一份很長的判決不能佔滿候選（少數見解 45 段壓過多數見解） ----
def _long_judgment(s, citation, texts, case_types):
    j = Judgment(citation=citation, court="測試法院", court_level="district", decided_on=date(2024, 1, 1),
                 source_url="https://example.invalid/", full_text="".join(texts), status="active", curated=False,
                 case_types=case_types)
    s.add(j)
    s.flush()
    for i, t in enumerate(texts, start=1):
        s.add(JudgmentPassage(judgment_id=j.id, seq=i, passage_type="court_reasoning", text=t, status="active"))
    s.flush()
    return j


def test_minority_view_ranks_below_majority_but_still_appears(db):
    """M4：少數見解（註記開頭「少數見解」）降權：同樣相關時排在多數見解後面，但不會被排除。"""
    from app.corpus.search import MINORITY_WEIGHT
    text = "配偶身分法益受侵害，得請求非財產上損害之相當金額。"
    with admin_session() as s:
        minority = _long_judgment(s, "少數判決", [text], ["spousal_rights"])
        minority.annotation = "少數見解：否定「配偶權」為法律上權利"
        majority = _long_judgment(s, "多數判決", [text], ["spousal_rights"])
        s.flush()
        got = [p.judgment_id for p in search_passages(s, text, 5, case_type="spousal_rights")]
        assert got == [majority.id, minority.id] and MINORITY_WEIGHT == 0.6
        majority.annotation = "另有見解可參"  # 不是少數見解的註記不降權
        s.flush()
        assert len(search_passages(s, text, 5, case_type="spousal_rights")) == 2


# ---- 計畫 1B 驗收：用 9 件真 AI 驗收時拆出的主張重播候選建立（不呼叫 AI） ----
ACCEPTANCE = Path(__file__).resolve().parents[2] / "docs" / "acceptance"
RECORDED = [ACCEPTANCE / "e2e_report_1b_run1.json", ACCEPTANCE / "e2e_report_1b_run2.json",
            ACCEPTANCE / "e2e_report_1b_run3.json", ACCEPTANCE / "e2e_report_1b_run4.json",
            ACCEPTANCE / "e2e_report_1b.json"]  # 每次真 AI 拆出的主張不同，全部重播
NEW_TYPE_CASES = ["spike-affair-1663", "spike-estate-159", "spike-nominee-524", "affair-2", "estate-2", "nominee-2"]


def test_other_type_share_on_recorded_claims(db):
    """設計第 6 節：新案型的候選中，其他類型判決的比例低於 30%；同時不能把他類完全排除（設計 5.3）。"""
    from app.pipeline.candidates import PASSAGES_PER_CLAIM
    with admin_session() as s:
        load_seed(s, Path(__file__).resolve().parents[1] / "seed")
        tags = {j.id: j.case_types or [] for j in s.scalars(select(Judgment))}
        shares = {}
        for path in RECORDED:
            report = json.loads(path.read_text(encoding="utf-8"))
            for name in NEW_TYPE_CASES:
                r = report[name]
                seen = {p.id: p for c in r["claims"]
                        for p in search_passages(s, f"{c['summary']} {c['quote']}", PASSAGES_PER_CLAIM,
                                                 case_type=r["case_type"])}
                shares[f"{path.stem}:{name}"] = (sum(r["case_type"] not in tags[p.judgment_id] for p in seen.values())
                                                 / len(seen))
    assert all(v < 0.30 for v in shares.values()), shares
    assert any(v > 0 for v in shares.values()), shares  # 只加權、不排除


def test_same_type_minority_still_outranks_other_types(db):
    """同類的少數見解（降權後）仍要排在其他類型的精選判決前面：同類多數 > 同類少數 > 他類。"""
    text = "非財產上損害之賠償，應審酌雙方身分資力與加害程度定相當之金額。"
    with admin_session() as s:
        other = _long_judgment(s, "他類精選", [text], ["traffic_tort"])  # 先建：同分時會排前面
        other.curated = True
        minority = _long_judgment(s, "同類少數", [text], ["spousal_rights"])
        minority.annotation = "少數見解：否定「配偶權」為法律上權利"
        s.flush()
        got = [p.judgment_id for p in search_passages(s, text, 5, case_type="spousal_rights")]
        assert got.index(minority.id) < got.index(other.id)


def test_other_type_best_match_keeps_a_slot(db):
    """最終審查 I3：遺產案裡的借名爭點，借名判決在文字上最相關時，同類加權不能把它整個擠出候選（設計：只加權、不排除）。"""
    query = "主張借名登記者應就借名契約之成立負舉證責任"
    with admin_session() as s:
        nominee = _long_judgment(s, "借名判決", [query], ["nominee_registration"])
        _long_judgment(s, "遺產判決", [f"主張借名登記者，遺產分割方法第{i}項另議" for i in range(1, 10)],
                       ["estate_partition"])
        got = search_passages(s, query, 8, case_type="estate_partition")
        assert len(got) == 8 and nominee.id in {p.judgment_id for p in got}
        # 同類判決文字上一樣相關時，不另外保留他類的位置
        same = _long_judgment(s, "遺產判決二", [query], ["estate_partition"])
        got = search_passages(s, query, 8, case_type="estate_partition")
        assert got[0].judgment_id == same.id and nominee.id not in {p.judgment_id for p in got}


@pytest.mark.parametrize("number,word", [("113年度台上字第1號", "台上"), ("113年度簡上字第2號", "簡上"),
                                         ("113年度消債字第3號", "消債"), ("113年度司票字第4號", "司票"),
                                         ("113消債5", "消債"), ("113司拍5", "司拍")])
def test_real_civil_case_words_still_read(number, word):
    """暫代文字擋掉之後，真的民事字別（含簡寫格式）仍要讀得出來。"""
    assert procedure_for("estate_partition", number) == "civil"
    assert word in procedure_warning("estate_partition", number)


# ---- 批次 B：簡寫案號前面帶法院名稱也要讀得出字別 ----
@pytest.mark.parametrize("number,word,procedure", [
    ("臺灣臺北地方法院113家繼訴89", "家繼訴", "family"),
    ("臺灣高等法院112上易123", "上易", "civil"),
    ("臺灣高等法院臺中分院112上易123", "上易", "civil"),
    ("臺灣高等法院 臺中分院 112 上易 123", "上易", "civil"),
    ("臺灣臺北地方法院113年度家訴字第5號", "家訴", "family"),
    ("最高法院112台上123", "台上", "civil"),
    ("臺灣高雄少年及家事法院113訴5", "訴", "civil"),  # 法院名稱裡的「家」不是字別
])
def test_short_case_number_with_court_prefix_reads_word(number, word, procedure):
    assert procedure_for("traffic_tort" if procedure == "family" else "estate_partition", number) == procedure
    ct = "traffic_tort" if procedure == "family" else "estate_partition"
    warning = procedure_warning(ct, number)
    assert warning is not None and "無法判讀" not in warning and f"「{word}」" in warning


@pytest.mark.parametrize("number", ["113年度監宣字第5號", "113年度輔宣字第6號", "113年度司養聲字第7號",
                                    "113年度司繼字第8號", "113監宣抗9", "臺灣臺北地方法院113司繼10"])
def test_family_words_without_jia_are_family(number):
    """使用者決定（2026-11-26）：監宣、輔宣、司養聲、司繼依家事事件法屬家事事件，書狀用家事程序。"""
    assert procedure_for("traffic_tort", number) == "family"
    assert procedure_warning("estate_partition", number) is None  # 分割遺產預設就是家事：不必提醒


@pytest.mark.parametrize("number", ["113年度司促字第1號", "113年度司執字第2號", "113年度司聲字第3號"])
def test_other_si_words_stay_civil(number):
    assert procedure_for("estate_partition", number) == "civil"
