"""要旨真實 AI 驗收（計畫 3 丙 Task 9）：在開發資料庫用真的 AI 對指定判決產生要旨，印出耗時、用量、
AI 原本回了幾句、通過檢查幾句；--out 把通過檢查的要旨存成 {判決字號: {issues, excerpts}}，給 e2e 重播寫回用。

會用到本機 Claude Code 訂閱額度。費用記在 --member 那位成員的事務所與其第一個案件。

用法：
  cd backend && PYTHONUTF8=1 PYTHONPATH=. uv run python scripts/accept_summaries.py --member walker@dev.test \\
      --citation 臺灣臺中地方法院113年度簡上字第211號民事判決 [--citation ...] [--out ../docs/acceptance/x.json]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sqlalchemy import func, select

from app.ai.provider import get_provider
from app.config import Settings
from app.corpus.summary import EXCERPT_MAX, EXCERPT_MIN, claim, reasoning_passages, run_summary, summary_view
from app.text_norm import find_quote, normalize_text
from app.verify.citations import _in_party_claim
from app.db import admin_session, configure
from app.models import Case, Judgment, JudgmentSummary, Member


class _Capture:
    """包住真的 provider，記下 AI 原本回了什麼（才算得出被檢查丟掉幾句）。"""

    def __init__(self, inner):
        self.inner, self.last = inner, None

    def run(self, **kw):
        self.last = self.inner.run(**kw)
        return self.last

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _why(e: dict, passages: dict) -> str:
    """節錄句沒通過檢查的原因（與 summary.validated 同順序）。"""
    p = passages.get(e.get("passage_seq"))
    text = e.get("text")
    if p is None or not isinstance(text, str):
        return "段號不是法院理由段"
    if not EXCERPT_MIN <= len(normalize_text(text)) <= EXCERPT_MAX:
        return f"長度 {len(normalize_text(text))} 字不合"
    if find_quote(p.text, text) is None:
        return "原文找不到（不是逐字）"
    if _in_party_claim(p.text, text):
        return "像當事人主張"
    return "超過句數上限或重複"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--member", required=True)
    ap.add_argument("--citation", action="append", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    settings = Settings()
    configure(settings)
    with admin_session() as s:
        m = s.scalars(select(Member).where(func.lower(Member.email) == a.member.lower())).one()
        case_id = s.scalars(select(Case.id).where(Case.firm_id == m.firm_id).order_by(Case.created_at)).first()
        firm_id, member_id = m.firm_id, m.id
    saved: dict[str, dict] = {}
    for citation in a.citation:
        with admin_session() as s:
            j = s.scalars(select(Judgment).where(Judgment.citation == citation)).one()
        token = claim(j, retry=True, force=True)
        if token is None:
            print(f"== {citation}: 正在由別的程序產生，略過")
            continue
        holder: dict = {}

        def factory(settings_, firm, holder=holder):
            holder["p"] = _Capture(get_provider(settings_, firm))
            return holder["p"]
        t0 = time.monotonic()
        run_summary(j.id, token, firm_id, case_id, member_id, settings, factory)
        secs = time.monotonic() - t0
        raw = holder["p"].last.data if holder.get("p") and holder["p"].last else {}
        usage = holder["p"].last.usage if holder.get("p") and holder["p"].last else None
        with admin_session() as s:
            row = s.scalars(select(JudgmentSummary).where(JudgmentSummary.judgment_id == j.id)).one()
            view = summary_view(s, s.get(Judgment, j.id))
        print(f"== {citation}（{secs:.0f} 秒；用量 {usage}）")
        print(f"   狀態 {row.status}／{view['status']}；錯誤種類 {row.error_kind}")
        print(f"   AI 回：爭點 {raw.get('issues')}，節錄 {len(raw.get('excerpts') or [])} 句；"
              f"通過檢查：爭點 {view['issues']}，節錄 {len(view['excerpts'])} 句")
        for e in view["excerpts"]:
            print(f"   [段{e['passage_seq']}] {e['text']}")
        kept = {(e["passage_seq"], e["text"]) for e in view["excerpts"]}
        with admin_session() as s:
            passages = {p.seq: p for p in reasoning_passages(s, j.id)}
        for e in raw.get("excerpts") or []:
            if isinstance(e, dict) and (e.get("passage_seq"), e.get("text")) not in kept:
                print(f"   [丟掉：{_why(e, passages)}][段{e.get('passage_seq')}] {e.get('text')}")
        if view["status"] == "ok":
            saved[citation] = {"issues": view["issues"],
                               "excerpts": [{"passage_seq": e["passage_seq"], "text": e["text"]} for e in view["excerpts"]]}
    if a.out:
        Path(a.out).write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"saved {len(saved)} -> {a.out}")


if __name__ == "__main__":
    main()
