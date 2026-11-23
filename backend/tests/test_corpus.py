import json
import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import func, select

from app.corpus.search import article_display, bigrams, search_articles, search_pages, search_passages
from app.corpus.seed_loader import load_seed
from app.corpus.splitter import split_judgment
from app.corpus.statute_source import parse_law_single
from app.db import admin_session, app_session, tenant_session
from app.ingest.service import ingest_document
from app.models import Case, Judgment, JudgmentPassage, StatuteArticle
from app.text_norm import normalize_text
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
    assert len(files) == 3
    total = 0
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for art in data["articles"]:
            raw = SEED / "raw" / "statutes" / f"{data['pcode']}_{art['article_no']}.html"
            parsed = parse_law_single(raw.read_text(encoding="utf-8"))
            assert parsed == {"name": data["name"], "article_no": art["article_no"], "text": art["text"]}
            total += 1
    assert total == 12


def test_seed_judgments_match_raw_pdf():
    files = sorted((SEED / "judgments").glob("*.json"))
    assert len(files) == 5
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["full_text"] == pdf_text(SEED / "raw" / data["raw_file"])
        assert data["curated"] is True
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
        assert s.scalar(select(func.count()).select_from(Judgment)) == 5
        assert s.scalar(select(func.count()).select_from(StatuteArticle)) == 12
    assert first == (5, 12)
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
    firm = create_firm("甲事務所")
    with tenant_session(firm) as s:
        c1 = Case(firm_id=firm, title="甲案", our_side="plaintiff")
        c2 = Case(firm_id=firm, title="乙案", our_side="plaintiff")
        s.add_all([c1, c2])
        s.flush()
        ingest_document(s, firm, c1.id, "evidence", "診斷.txt", "診斷證明書：右腕骨折，宜休養六週。".encode())
        ingest_document(s, firm, c2.id, "evidence", "他案.txt", "診斷證明書：右腕骨折，宜休養六週。".encode())
        ingest_document(s, firm, c1.id, "opponent_brief", "答辯.txt", "右腕骨折休養六週過長".encode())
        hits = search_pages(s, c1.id, "右腕 骨折 休養", k=5)
        assert len(hits) == 1 and "診斷證明書" in hits[0].text


def test_page_footers_removed_in_all_seed_judgments():
    for f in sorted((SEED / "judgments").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for _, text in split_judgment(data["full_text"]):
            for line in text.split("\n"):
                assert not re.fullmatch(r"\s*第\s*[一二三四五六七八九十百\d]+\s*頁\s*", line), (f.name, line)


def test_procedural_tail_is_other():
    kinds = {k for k, t in passages_3946() if "職權宣告假執行" in normalize_text(t)}
    assert kinds == {"other"}
