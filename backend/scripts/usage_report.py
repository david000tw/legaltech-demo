"""依步驟彙總 usage_records，並以設計文件第 6 節的 Opus 5.5 單價換算美元。

uv run python -X utf8 scripts/usage_report.py [--db postgresql+psycopg://postgres:devpass@127.0.0.1:55432/legaltech]
本機 Claude Code 的數字僅供參考（訂閱方案不按 token 計費，快取行為也與 Bedrock 不同）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import Settings  # noqa: E402

# 每百萬 token 美元（spec 第 6 節）
PRICE = {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 8.0}

SQL = """
select step, count(*) as calls,
       sum(input_tokens) as input, sum(output_tokens) as output,
       sum(cache_read_tokens) as cache_read, sum(cache_write_tokens) as cache_write
from usage_records group by step order by min(created_at)
"""


def cost(row) -> float:
    return sum(row[k] * PRICE[k] for k in PRICE) / 1_000_000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=Settings().database_admin_url)
    args = ap.parse_args()
    eng = create_engine(args.db)
    with eng.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(text(SQL))]
    print("| 步驟 | 次數 | 平均讀入 | 平均快取讀 | 平均快取寫 | 平均寫出 | 合計美元（換算） |")
    print("|---|---|---|---|---|---|---|")
    total = 0.0
    for r in rows:
        n = r["calls"]
        usd = cost(r)
        total += usd
        print(f"| {r['step']} | {n} | {r['input'] // n:,} | {r['cache_read'] // n:,} | {r['cache_write'] // n:,} | "
              f"{r['output'] // n:,} | {usd:.2f} |")
    print(f"\n合計約 {total:.2f} 美元（本機 Claude Code 數字僅供參考）")


if __name__ == "__main__":
    main()
