"""從 seed/raw 的原始判決 PDF 產生種子 JSON（內文由 pypdf 抽出，不經任何人工或模型轉寫）。

uv run python -X utf8 scripts/build_seed_judgment.py seed/raw/xxx.pdf --citation ... --court ...
    --court-level summary|district|high|supreme --decided-on YYYY-MM-DD --url https://...
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader

SEED = Path(__file__).resolve().parent.parent / "seed"


def pdf_full_text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--citation", required=True)
    ap.add_argument("--court", required=True)
    ap.add_argument("--court-level", required=True, choices=["summary", "district", "high", "supreme"])
    ap.add_argument("--decided-on", required=True)
    ap.add_argument("--url", required=True)
    a = ap.parse_args()
    pdf = a.pdf.resolve()
    if pdf.parent != (SEED / "raw").resolve():
        raise SystemExit("原始 PDF 必須放在 seed/raw/")
    data = {"citation": a.citation, "court": a.court, "court_level": a.court_level,
            "decided_on": a.decided_on, "source_url": a.url, "curated": True,
            "raw_file": pdf.name, "full_text": pdf_full_text(pdf)}
    out = SEED / "judgments" / f"{pdf.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out.name, len(data["full_text"]), "chars")


if __name__ == "__main__":
    main()
