"""計畫 2B 驗收（設計 8.2）：33 組標註好的「原文＋書狀句子」，用開發者本機的 Claude Code 跑支持度檢查，
算混淆矩陣、檢查通過條件，寫進 docs/acceptance-plan-2b.md。

會用訂閱額度（約 5 次呼叫），預設不跑：
  LEGALTECH_TEST_DB=legaltech_test_impl PYTHONUTF8=1 uv run pytest -q -s -m real_ai tests/support_acceptance
先檢查流程（不花額度）：加 SUPPORT_ACCEPTANCE_DRY=1，改用假的 AI 回標準答案，報告不寫入 docs。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app import db as dbmod
from app.ai.provider import get_provider
from app.db import tenant_session
from app.models import SUPPORT_VERDICTS, Case, Firm, SupportCheck, UsageRecord
from app.support.job import SUPPORT_BATCH, run_support_checks
from tests.conftest import make_settings
from tests.helpers import create_firm

pytestmark = [pytest.mark.real_ai, pytest.mark.support_real]

APP = Path(__file__).resolve().parents[3]
PAIRS = Path(__file__).with_name("pairs.json")
REPORT = APP / "docs/acceptance-plan-2b.md"
RAW = APP / "docs/acceptance/support_acceptance_2b.json"
AUTO_START, AUTO_END = "<!-- auto:start -->", "<!-- auto:end -->"
RED = ("not_supported", "party_claim_misuse")
ZH = {"supports": "支持", "partial": "部分支持", "not_supported": "不支持", "party_claim_misuse": "誤用當事人主張",
      None: "（沒有結果）"}
DRAFT_CITATIONS = 45  # 推估用：真實起草的書狀每份 33～57 筆引用
_BLOCK = re.compile(r"【引用 (C\d+)】(.*?)(?=【引用 C\d+】|\Z)", re.S)


class _Timed:
    """記下每次呼叫的秒數。"""
    def __init__(self, inner):
        self.inner, self.seconds = inner, []

    def run(self, **kw):
        t = time.monotonic()
        try:
            return self.inner.run(**kw)
        finally:
            self.seconds.append(time.monotonic() - t)


def _dry_provider(pairs):
    from tests.fakes.fake_provider import FakeProvider

    def handler(call):
        items = []
        for code, block in _BLOCK.findall(call["user"]):
            sentence = block.split("【書狀句子】", 1)[1].strip()
            gold = next(p["gold"] for p in pairs if p["sentence"] == sentence)
            items.append({"id": code, "verdict": gold, "explanation": "（檢查流程：標準答案）"})
        return {"items": items}
    return FakeProvider({"support": handler})


def _write_report(summary: dict, rows: list[dict]) -> None:
    m = summary["matrix"]
    head = ["gold ＼ AI"] + [ZH[v] for v in SUPPORT_VERDICTS] + [ZH[None]]
    lines = [AUTO_START, "", f"執行時間：{summary['ran_at']}（本機 Claude Code，{summary['calls']} 次呼叫）", "",
             "### 通過條件", "", "| 項目 | 結果 | 通過條件 | 判定 |", "|---|---|---|---|",
             f"| 「不支持」「誤用當事人主張」全部標紅 | 漏 {summary['red_missed']} 組（共 {summary['red_total']} 組） | 漏 0 | "
             f"{'通過' if summary['red_missed'] == 0 else '未通過'} |",
             f"| 「支持」被誤標成紅色 | {summary['supports_red']} 組（共 {summary['supports_total']} 組） | ≤1 | "
             f"{'通過' if summary['supports_red'] <= 1 else '未通過'} |",
             f"| 四種結果完全一致 | {summary['exact']}／{summary['total']} | 記錄 | — |",
             f"| 沒有結果（尚未檢查） | {summary['failed']} 組 | 記錄 | — |", "",
             "### 混淆矩陣（列：標註；欄：AI 判斷）", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for g in SUPPORT_VERDICTS:
        lines.append(f"| {ZH[g]} | " + " | ".join(str(m[g].get(v, 0)) for v in (*SUPPORT_VERDICTS, None)) + " |")
    lines += ["", "### 用量與時間", "", "| 項目 | 數值 |", "|---|---|",
              f"| 呼叫次數（每批最多 {SUPPORT_BATCH} 筆） | {summary['calls']} |",
              f"| 每次呼叫秒數（平均／最長） | {summary['sec_avg']:.0f}／{summary['sec_max']:.0f} |",
              f"| 輸入 token（含快取讀寫）合計 | {summary['input_tokens']:,} |",
              f"| 輸出 token 合計 | {summary['output_tokens']:,} |",
              f"| 每組平均輸入 token | {summary['input_per_pair']:,.0f} |",
              f"| 推估一份 {DRAFT_CITATIONS} 筆引用的書狀 | 約 {summary['est_calls']} 次呼叫、輸入約 "
              f"{summary['est_input']:,.0f} token、約 {summary['est_minutes']:.0f} 分鐘（設計 7.4 原估 3 萬輸入） |", "",
              "推估方式：每組平均輸入 token × 引用數、每批平均秒數 × 批數；真實書狀同一份原文常被引用多次，"
              "會合併送出，實際可能較低。", "",
              "### 每組結果", "", "| id | 種類 | 標註 | AI 判斷 | 判定 | 說明摘錄 |", "|---|---|---|---|---|---|"]
    for r in rows:
        ok = "一致" if r["ai"] == r["gold"] else ("紅黃方向一致" if (r["ai"] in RED) == (r["gold"] in RED) else "不一致")
        lines.append(f"| {r['id']} | {r['kind']} | {ZH[r['gold']]} | {ZH[r['ai']]} | {ok} | "
                     f"{(r['explanation'] or r['error'] or '').replace('|', '／')[:60]} |")
    lines += ["", AUTO_END]
    block = "\n".join(lines)
    if REPORT.exists():
        old = REPORT.read_text(encoding="utf-8")
        if AUTO_START in old and AUTO_END in old:
            new = old[:old.index(AUTO_START)] + block + old[old.index(AUTO_END) + len(AUTO_END):]
        else:
            new = old.rstrip() + "\n\n" + block + "\n"
    else:
        new = ("# 計畫 2B 驗收紀錄：支持度檢查\n\n依設計第 8.2 節。自動驗收由 "
               "`tests/support_acceptance/test_support_acceptance.py` 產生下面這一段；瀏覽器實測寫在後面。\n\n"
               "## 自動驗收（33 組標註好的引用）\n\n" + block + "\n")
    REPORT.write_text(new, encoding="utf-8")


def test_support_acceptance(db, tmp_path):
    dry = os.environ.get("SUPPORT_ACCEPTANCE_DRY") == "1"
    settings = make_settings(tmp_path)
    dbmod.configure(settings)
    pairs = json.loads(PAIRS.read_text(encoding="utf-8"))
    firm = create_firm("驗收事務所", internal=True)
    with tenant_session(firm) as s:
        case = Case(firm_id=firm, title="支持度驗收", our_side="plaintiff")
        s.add(case)
        s.flush()
        by_id = {}
        for p in pairs:
            row = SupportCheck(firm_id=firm, case_id=case.id, fingerprint=hashlib.sha256(p["id"].encode()).hexdigest(),
                               kind=p["kind"], label=p["label"], quote=p["quote"], sentence=p["sentence"],
                               source=p["source"], status="pending")
            s.add(row)
            s.flush()
            by_id[p["id"]] = row.id
        provider = _Timed(_dry_provider(pairs) if dry else get_provider(settings, s.get(Firm, firm)))

    run_support_checks(firm, list(by_id.values()), settings, provider=provider)

    with tenant_session(firm) as s:
        got = {r.id: r for r in s.scalars(select(SupportCheck))}
        usage = list(s.scalars(select(UsageRecord).where(UsageRecord.step == "support")))
        rows = [{"id": p["id"], "kind": p["kind"], "gold": p["gold"], "ai": got[by_id[p["id"]]].verdict,
                 "status": got[by_id[p["id"]]].status, "explanation": got[by_id[p["id"]]].explanation,
                 "error": got[by_id[p["id"]]].error, "origin": p["origin"]} for p in pairs]
        input_tokens = sum(u.input_tokens + u.cache_read_tokens + u.cache_write_tokens for u in usage)
        output_tokens = sum(u.output_tokens for u in usage)
    matrix = {g: Counter(r["ai"] for r in rows if r["gold"] == g) for g in SUPPORT_VERDICTS}
    red_rows = [r for r in rows if r["gold"] in RED]
    sup_rows = [r for r in rows if r["gold"] == "supports"]
    secs = provider.seconds or [0.0]
    calls = len(provider.seconds)
    est_calls = math.ceil(DRAFT_CITATIONS / SUPPORT_BATCH)
    summary = {
        "ran_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"), "dry": dry,
        "total": len(rows), "exact": sum(r["ai"] == r["gold"] for r in rows),
        "failed": sum(r["status"] != "done" for r in rows),
        "red_total": len(red_rows), "red_missed": sum(r["ai"] not in RED for r in red_rows),
        "supports_total": len(sup_rows), "supports_red": sum(r["ai"] in RED for r in sup_rows),
        "matrix": {g: dict(c) for g, c in matrix.items()},
        "calls": calls, "sec_avg": sum(secs) / len(secs), "sec_max": max(secs),
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "input_per_pair": input_tokens / len(rows),
        "est_calls": est_calls, "est_input": input_tokens / len(rows) * DRAFT_CITATIONS,
        "est_minutes": sum(secs) / len(secs) * est_calls / 60,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "matrix"}, ensure_ascii=False))
    if not dry:
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        _write_report(summary, rows)
    assert summary["failed"] == 0, [r for r in rows if r["status"] != "done"]
    assert summary["red_missed"] == 0, [r for r in red_rows if r["ai"] not in RED]
    assert summary["supports_red"] <= 1, [r for r in sup_rows if r["ai"] in RED]
