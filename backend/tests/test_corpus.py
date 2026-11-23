import json
import re

import pytest
from datetime import date
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import func, select

from app.corpus.search import article_display, bigrams, search_articles, search_pages, search_passages
from app.corpus.seed_loader import load_seed
from app.corpus.splitter import split_judgment, split_problems
from app.corpus.statute_source import parse_law_single
from app.db import admin_session, app_session, tenant_session
from app.ingest.service import ingest_document
from app.models import Case, Judgment, JudgmentPassage, Page, PageText, StatuteArticle
from app.text_norm import normalize_text
from scripts.build_seed_statutes import api_articles
from tests.helpers import create_firm

BACKEND = Path(__file__).resolve().parent.parent
SEED = BACKEND / "seed"
FIXTURE = BACKEND / "tests" / "fixtures" / "tcev_113_3946.pdf"


def pdf_text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def passages_3946():
    return split_judgment(pdf_text(FIXTURE))


def test_seed_statutes_match_raw_snapshot():
    files = sorted((SEED / "statutes").glob("*.json"))
    assert len(files) == 6  # spike：加民事訴訟法、家事事件法、保險法
    total = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for art in data["articles"]:
            raw = SEED / "raw" / "statutes" / f"{data['pcode']}_{art['article_no']}.html"
            if not raw.exists():  # spike：新條文的原始快照是開放資料 API（seed/raw/statutes/moj_api）
                assert api_articles(data["pcode"])[art["article_no"]] == art["text"]
                total += 1
                continue
            parsed = parse_law_single(raw.read_text(encoding="utf-8"))
            assert parsed == {"name": data["name"], "article_no": art["article_no"], "text": art["text"]}
            total += 1
    assert total == 42  # spike：12 + 30


def test_seed_judgments_match_raw_pdf():
    files = sorted((SEED / "judgments").glob("*.json"))
    assert len(files) == 15  # spike：加外遇、分割遺產、借名登記各 3～4 件
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["full_text"] == pdf_text(SEED / "raw" / data["raw_file"])
        assert isinstance(data["curated"], bool)  # spike：每類只精選 1～2 件
        assert data["court_level"] in {"summary", "district", "high", "supreme"}


def test_split_3946_has_court_reasoning_with_known_sentence():
    target = normalize_text("未在適當位置暫停禮讓幹線道之系爭車輛")
    hits = [t for kind, t in passages_3946() if target in normalize_text(t)]
    assert hits
    assert all(kind == "court_reasoning" for kind, t in passages_3946() if target in normalize_text(t))


def test_split_3946_defendant_claim_is_party_claim():
    kinds = {kind for kind, t in passages_3946() if "原告超速" in normalize_text(t)}
    assert kinds == {"party_claim"}


def test_split_3946_main_text_and_reasoning_count():
    ps = passages_3946()
    main = [t for k, t in ps if k == "main_text"]
    assert any("67,445" in normalize_text(t) for t in main)
    assert sum(1 for k, _ in ps if k == "court_reasoning") >= 3


def test_line_numbers_removed():
    for _, text in passages_3946():
        assert not any(re.fullmatch(r"\s*\d{2}\s*", line) for line in text.split("\n"))


def test_article_display_191_2():
    assert article_display("民法", "191-2") == "民法第191條之2"
    assert article_display("民法", "184") == "民法第184條"


def test_bigrams_keep_cjk_and_digits_only():
    assert bigrams("支線 道，A1") == {"支線", "線道", "道1"}


def test_parse_law_single_html():
    html = ('<th>法規名稱：</th><td><a href="#">民法</a> <a>EN</a></td>'
            '<div class="col-no">第 191-2 條</div><div class="col-data"><div class="law-article">'
            '<div class="line-0000 show-number">第一項&amp;內容。</div>'
            '<div class="line-0004">第二項。</div></div></div>')
    assert parse_law_single(html) == {"name": "民法", "article_no": "191-2", "text": "第一項&內容。\n第二項。"}


def test_load_seed_idempotent(db):
    with admin_session() as s:
        first = load_seed(s, SEED)
    with admin_session() as s:
        second = load_seed(s, SEED)
        assert s.scalar(select(func.count()).select_from(Judgment)) == 15  # spike：5 → 15
        assert s.scalar(select(func.count()).select_from(StatuteArticle)) == 42  # spike：12 → 42
    assert first == (15, 42)
    assert second == (0, 0)


def test_search_finds_yield_rule(db):
    with admin_session() as s:
        load_seed(s, SEED)
    with app_session() as s:
        hits = search_passages(s, "支線道 暫停 讓 幹線道", k=3)
        assert all(h.passage_type == "court_reasoning" for h in hits)
        target = normalize_text("未在適當位置暫停禮讓幹線道之系爭車輛")
        assert any(target in normalize_text(h.text) for h in hits)


def test_search_excludes_withdrawn(db):
    with admin_session() as s:
        j = Judgment(citation="測試法院100年度測字第1號民事判決", court="測試法院", court_level="district",
                     decided_on=date(2025, 1, 1), source_url="x", full_text="x", status="withdrawn")
        s.add(j)
        s.flush()
        s.add(JudgmentPassage(judgment_id=j.id, seq=1, passage_type="court_reasoning",
                              text="支線道車應暫停讓幹線道車先行", status="active"))
    with app_session() as s:
        assert search_passages(s, "支線道 暫停 讓 幹線道", k=5) == []


def test_search_articles_finds_191_2(db):
    with admin_session() as s:
        load_seed(s, SEED)
    with app_session() as s:
        hits = search_articles(s, "動力車輛 使用中 加損害於他人 駕駛人 賠償", k=3)
        assert ("民法", "191-2") in [(a.statute.name, a.article_no) for a in hits]


def test_search_pages_skips_scanned_and_other_cases(db):
    from tests.test_ingest import blank_pdf
    firm = create_firm("甲事務所")
    with tenant_session(firm) as s:
        c1 = Case(firm_id=firm, title="甲案", our_side="plaintiff")
        c2 = Case(firm_id=firm, title="乙案", our_side="plaintiff")
        s.add_all([c1, c2])
        s.flush()
        d = ingest_document(s, firm, c1.id, "evidence", "診斷.txt", "診斷證明書：右腕骨折，宜休養六週。".encode())
        ingest_document(s, firm, c1.id, "evidence", "掃描.pdf", blank_pdf())
        ingest_document(s, firm, c2.id, "evidence", "他案.txt", "診斷證明書：右腕骨折，宜休養六週。".encode())
        ingest_document(s, firm, c1.id, "opponent_brief", "答辯.txt", "右腕骨折休養六週過長".encode())
        page = s.scalars(select(Page).where(Page.document_id == d.id)).one()
        s.add(PageText(firm_id=firm, page_id=page.id, version=2, source="manual",
                       text="診斷證明書（校正版）：右腕骨折，宜休養六週。"))
        s.flush()
        hits = search_pages(s, c1.id, "右腕 骨折 休養", k=5)
        assert len(hits) == 1 and hits[0].version == 2 and "校正版" in hits[0].text


def test_page_footers_removed_in_all_seed_judgments():
    for f in sorted((SEED / "judgments").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for _, text in split_judgment(data["full_text"]):
            for line in text.split("\n"):
                assert not re.fullmatch(r"\s*第\s*[一二三四五六七八九十百\d]+\s*頁\s*", line), (f.name, line)


def test_procedural_tail_is_other():
    kinds = {k for k, t in passages_3946() if "職權宣告假執行" in normalize_text(t)}
    assert kinds == {"other"}


def _all_passages():
    for f in sorted((SEED / "judgments").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        yield f.stem, split_judgment(data["full_text"])


def test_sentences_not_split_mid_line():
    """換行後剛好以「數字、」開頭的行，不能被當成新段落（竹院簡上17）。"""
    for needle in ("就本件車禍應各負百分之70、百分之30之過失責任", "被上訴人依民法第184條第1項前段、第191條之2、第193條第1項"):
        n = normalize_text(needle)
        holders = [(stem, k) for stem, ps in _all_passages() for k, t in ps if n in normalize_text(t)]
        assert holders == [("SCDV_114_jianshang_17", "court_reasoning")], (needle, holders)


@pytest.mark.parametrize("stem,needle,kind", [
    ("tcev_113_3946", "得心證之理由", "court_reasoning"),
    ("tcev_113_3946", "一、原告主張", "party_claim"),
    ("TPEV_114_beijian_610", "得心證之理由", "court_reasoning"),
    ("TPEV_114_beijian_610", "原告起訴主張略以", "party_claim"),
    ("TPEV_114_beijian_610", "被告則以", "party_claim"),
    ("SDEV_114_shajian_456", "原告起訴主張", "party_claim"),
    ("SDEV_114_shajian_456", "二、被告抗辯", "party_claim"),
    ("SDEV_114_shajian_456", "尚無依強制汽車責任保險法第32條扣除之問題", "court_reasoning"),
    ("TCDV_113_jianshang_211", "法院之判斷", "court_reasoning"),
    ("TCDV_113_jianshang_211", "兩造不爭執事項", "other"),
    ("SCDV_114_jianshang_17", "被上訴人起訴主張", "party_claim"),
    ("SCDV_114_jianshang_17", "上訴人則以", "party_claim"),
    ("SCDV_114_jianshang_17", "本院之判斷", "court_reasoning"),
])
def test_splitter_classifies_headings_in_all_seed_judgments(stem, needle, kind):
    passages = dict(_all_passages())[stem]
    kinds = {k for k, t in passages if normalize_text(needle) in normalize_text(t)}
    assert kinds == {kind}, (stem, needle, kinds)


def test_seed_metadata_matches_pdf_text():
    for f in sorted((SEED / "judgments").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        head = normalize_text(d["full_text"][:300])
        number = re.search(r"\d+年度.+?字第\d+號", d["citation"]).group(0)
        assert number in head, f.name
        dates = re.findall(r"中華民國(\d+)年(\d+)月(\d+)日", normalize_text(d["full_text"]))
        y, m, dd = (int(x) for x in dates[-1])
        assert date(y + 1911, m, dd).isoformat() == d["decided_on"], f.name


def test_load_seed_resplit_restores_passages(db):
    with admin_session() as s:
        load_seed(s, SEED)
        p = s.scalars(select(JudgmentPassage)).first()
        p.text = "被竄改"
    with admin_session() as s:
        load_seed(s, SEED, resplit=True)
        assert s.scalars(select(JudgmentPassage).where(JudgmentPassage.text == "被竄改")).all() == []


# ---- 計畫 1B Task 2：新判決切段與上架檢查（設計 5.4） ----

def _seed_full_text(stem: str) -> str:
    return json.loads((SEED / "judgments" / f"{stem}.json").read_text(encoding="utf-8"))["full_text"]


def _kinds_holding(passages, needle: str) -> set[str]:
    n = normalize_text(needle)
    return {k for k, t in passages if n in normalize_text(t)}


@pytest.mark.parametrize("seed,party_head,court_head", [
    ("TPDV_113_jiajisu_89", "壹、原告主張：被繼承人林淑美", "按各共有人，除法令另有規定外"),  # 壹貳標題、「按…」見解段
    ("PCDV_113_chongsu_548", "一、原告起訴主張：兩造於民國", "四、本件爭點應為"),  # 「頁＋全形空白」行尾；本件爭點開啟判斷區
    ("TYDV_113_su_2562", "原告主張略以：原告與", "三、按因故意或過失，不法侵害他人之權利者"),  # 「三、按…」見解段
])
def test_new_judgment_split(seed, party_head, court_head):
    full = _seed_full_text(seed)
    nf = normalize_text(full)
    # 防呆：字串在全文中唯一，否則正確的切段也可能過不了
    assert nf.count(normalize_text(party_head)) == 1 and nf.count(normalize_text(court_head)) == 1
    passages = split_judgment(full)
    assert _kinds_holding(passages, party_head) == {"party_claim"}, seed
    assert _kinds_holding(passages, court_head) == {"court_reasoning"}, seed


COUNTERCLAIM = """事實及理由
壹、本訴部分
一、原告主張：被告應返還借款新臺幣10萬元。
二、被告則以：該款項係贈與等語置辯。
三、得心證之理由：
(一)原告主張其交付10萬元予被告，業據提出匯款單為證，堪信為真。
(二)被告抗辯為贈與，未舉證以實其說，不足採。
貳、反訴部分
一、反訴原告主張：反訴被告應給付反訴原告代墊之醫療費用5萬元。
二、反訴被告則以：從未委託代墊等語。
三、得心證之理由：
(一)反訴原告未能證明代墊事實，其請求無理由。
"""


def test_counterclaim_resets_section():
    passages = split_judgment(COUNTERCLAIM)
    assert _kinds_holding(passages, "反訴原告主張：反訴被告應給付") == {"party_claim"}
    assert _kinds_holding(passages, "(一)原告主張其交付10萬元予被告") == {"court_reasoning"}
    assert _kinds_holding(passages, "反訴原告未能證明代墊事實") == {"court_reasoning"}


def test_every_seed_judgment_passes_split_check():
    whitelist = json.loads((SEED / "split_whitelist.json").read_text(encoding="utf-8"))
    assert all(entry.get("reason") for entry in whitelist.values())
    for f in sorted((SEED / "judgments").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        problems = split_problems(split_judgment(data["full_text"]), data["full_text"],
                                  data["citation"] in whitelist)
        assert problems == [], (f.stem, problems)


def test_split_problems_flags_missing_sections():
    only_claims = [("party_claim", "原告主張被告應賠償。")]
    assert "no_court_reasoning" in split_problems(only_claims, "x" * 100, False)
    assert split_problems(only_claims, "x" * 100, True) == []
    thin = [("party_claim", "原告主張" + "甲" * 200), ("court_reasoning", "經查屬實。")]
    assert "court_reasoning_too_short" in split_problems(thin, "甲" * 1000, False)


COUNTERCLAIM_ONE_LEVEL = """事實及理由
一、原告主張：被告應返還借款新臺幣10萬元。
二、被告則以：該款項係贈與等語置辯。
三、得心證之理由：原告主張交付10萬元，業據提出匯款單，堪信為真。
四、反訴原告主張：反訴被告應給付代墊醫療費用5萬元。
五、反訴被告則以：從未委託代墊等語。
六、反訴部分得心證之理由：反訴原告未能證明代墊事實。
"""


def test_counterclaim_heading_resets_single_level():
    passages = split_judgment(COUNTERCLAIM_ONE_LEVEL)
    assert _kinds_holding(passages, "四、反訴原告主張") == {"party_claim"}
    assert _kinds_holding(passages, "五、反訴被告則以") == {"party_claim"}
    assert _kinds_holding(passages, "反訴原告未能證明代墊事實") == {"court_reasoning"}
