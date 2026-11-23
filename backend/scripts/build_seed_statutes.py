"""下載全國法規資料庫的單條條文頁到 seed/raw/statutes，再由程式解析產生 seed/statutes/*.json。

uv run python -X utf8 scripts/build_seed_statutes.py --download   # 重新下載原始頁
uv run python -X utf8 scripts/build_seed_statutes.py              # 只從既有原始頁重建 JSON
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.corpus.statute_source import parse_law_single  # noqa: E402

SEED = HERE.parent / "seed"
RAW = SEED / "raw" / "statutes"
URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?"

# (法規名稱, pcode, 條號)；pcode 已於 2026-11-23 以全國法規資料庫查證。
STATUTES = [
    ("民法", "B0000001", ["184", "191-2", "193", "195", "196", "213", "215", "216", "217"]),
    ("道路交通安全規則", "K0040013", ["93", "102"]),
    ("強制汽車責任保險法", "G0390060", ["32"]),
]


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for _, pcode, articles in STATUTES:
        for no in articles:
            url = URL + urllib.parse.urlencode({"pcode": pcode, "flno": no})
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            (RAW / f"{pcode}_{no}.html").write_bytes(data)
            print("downloaded", pcode, no, len(data))
            time.sleep(0.5)


def build() -> None:
    out_dir = SEED / "statutes"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, pcode, articles in STATUTES:
        arts = []
        for no in articles:
            parsed = parse_law_single((RAW / f"{pcode}_{no}.html").read_text(encoding="utf-8"))
            if parsed["name"] != name or parsed["article_no"] != no:
                raise SystemExit(f"原始頁內容不符：{pcode}_{no} → {parsed['name']} {parsed['article_no']}")
            arts.append({"article_no": no, "text": parsed["text"], "effective_from": None})
        data = {"name": name, "pcode": pcode, "articles": arts}
        (out_dir / f"{pcode}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", pcode, len(arts), "articles")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    if ap.parse_args().download:
        download()
    build()
