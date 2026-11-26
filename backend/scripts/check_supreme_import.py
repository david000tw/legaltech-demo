"""唯讀檢查：一份司法院判決網址能不能被「貼網址加入判決」收下（計畫 3 丙 Task 4）。

依序跑加入判決時的同一套步驟（解析網址 → 下載 PDF → 抽字 → 判讀字號 → 切段 → 上架檢查），
印出各段類型與字數；不寫入任何資料庫。只做一次正常下載，不繞過任何防機器人機制。

用法：cd backend && PYTHONUTF8=1 PYTHONPATH=. uv run python scripts/check_supreme_import.py <網址> [...]
"""
from __future__ import annotations

import sys
from collections import Counter

from app.corpus.importer import (
    ImportRejected, citation_from_text, fetch_pdf, parse_judgment_url, pdf_text, pdf_url,
)
from app.corpus.splitter import split_judgment, split_problems


def check(url: str) -> bool:
    print(f"== {url}")
    try:
        ref = parse_judgment_url(url)
        text = pdf_text(fetch_pdf(pdf_url(ref)))
        print("citation:", citation_from_text(text, ref))
    except ImportRejected as e:
        print("rejected before splitting:", e.message)
        return False
    passages = split_judgment(text)
    chars = Counter()
    for kind, t in passages:
        chars[kind] += len(t)
    print("passages:", len(passages), dict(chars), "full text chars:", len(text))
    problems = split_problems(passages, text, False)
    print("split_problems:", problems or "none")
    return not problems


if __name__ == "__main__":
    results = [check(u) for u in sys.argv[1:]]
    print("importable:", results)
