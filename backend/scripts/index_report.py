"""判決索引人工核對清單（計畫 3 丙 Task 8；唯讀）。

列出每份判決法院理由段抓到的條文與被引用判決（含句子）、沒收下的字號與原因、沒接上法律名稱的條文，
以及「民法第184條」「民法第195條」「最高法院51年度台上字第223號」三組查詢的結果。

用法：cd backend && PYTHONUTF8=1 PYTHONPATH=. uv run python scripts/index_report.py > ../docs/acceptance/judgment_index_report.txt
"""
from __future__ import annotations

from sqlalchemy import select

from app.corpus import case_refs as cr
from app.corpus.article_refs import _ART, ArticleRef, extract_articles
from app.corpus.ref_index import article_results, judgment_cited_by
from app.db import admin_session
from app.models import Judgment, JudgmentCaseRef, JudgmentPassage
from app.text_norm import normalize_text


def _case_reasons(norm: str, court: str, own_key: str | None) -> list[str]:
    """規則二沒收下的字號與原因（與 extract_case_refs 同一套判斷）。"""
    out = []
    for group in cr._series(norm, cr._find(norm)):
        cr._resolve_courts(group, court)
        marked = any(f.marker for f in group)
        for f in group:
            cited = f.paren or (marked and not f.criminal)
            key = cr.make_key(f.court, f.year, f.word, f.number, criminal=f.criminal)
            ctx = norm[max(0, f.start - 12):f.end + 14]
            if f.excluded:
                out.append(f"  不收（卷宗、偵查或調解案號）：…{ctx}…")
            elif key == own_key:
                continue
            elif not cited:
                why = "刑事字號只接受括號式" if f.criminal and marked else "不是括號式或緊接式的引用見解"
                out.append(f"  不收（{why}）：…{ctx}…")
    return out


def main() -> None:
    with admin_session() as s:
        judgments = list(s.scalars(select(Judgment).order_by(Judgment.citation)))
        for j in judgments:
            print(f"\n===== {j.citation}（{j.status}；refs_version={j.refs_version}）")
            passages = s.scalars(select(JudgmentPassage).where(
                JudgmentPassage.judgment_id == j.id, JudgmentPassage.passage_type == "court_reasoning",
                JudgmentPassage.status == "active").order_by(JudgmentPassage.seq)).all()
            for p in passages:
                norm = normalize_text(p.text)
                arts = extract_articles(p.text)
                cases = cr.extract_case_refs(p.text, j.court, j.case_key)
                missed_cases = _case_reasons(norm, j.court, j.case_key)
                got = {(h.ref.article_no) for h in arts}
                missed_arts = []
                for m in _ART.finditer(norm):
                    no = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
                    if no not in got:
                        missed_arts.append(f"…{norm[max(0, m.start() - 14):m.end() + 4]}…")
                if not (arts or cases or missed_cases or missed_arts):
                    continue
                print(f" [段{p.seq}]")
                for h in arts:
                    print(f"  條文 {h.ref.display()}")
                for h in cases:
                    print(f"  引用 {h.display}（{h.key}）｜{h.sentence}")
                for x in missed_cases:
                    print(x)
                for x in missed_arts:
                    print(f"  條文沒收（法律名稱接不上或不在清單）：{x}")

        print("\n\n===== 查詢核對")
        for ref in (ArticleRef("民法", "184"), ArticleRef("民法", "195")):
            total, rows = article_results(s, ref, None, limit=100)
            print(f"\n{ref.display()}：{total} 份")
            for r in rows:
                print(f"  {r['citation']}｜{r['sentences'][0]}")
        key = "最高法院|51|台上|223"
        citing = sorted({s.get(Judgment, jid).citation for jid in s.scalars(
            select(JudgmentCaseRef.judgment_id).where(JudgmentCaseRef.key == key))})
        print(f"\n最高法院51年度台上字第223號 被 {len(citing)} 份判決引用：")
        for c in citing:
            print(f"  {c}")
        internal = [(j.citation, judgment_cited_by(s, j)) for j in judgments]
        print("\n庫內互相引用：", [(c, [x["citation"] for x in by]) for c, by in internal if by] or "無")


if __name__ == "__main__":
    main()
