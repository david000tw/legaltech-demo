"""從 seed/raw 的原始判決 PDF 產生種子 JSON（內文由 pypdf 抽出，不經任何人工或模型轉寫）。

uv run python -X utf8 scripts/build_seed_judgment.py seed/raw/xxx.pdf --citation ... --court ...
    --court-level summary|district|high|supreme --decided-on YYYY-MM-DD --url https://...
    --case-types spousal_rights[,traffic_tort] [--annotation "少數見解：…"]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from app.case_types import CASE_TYPES  # noqa: E402
from app.corpus.splitter import split_judgment, split_problems  # noqa: E402

SEED = BACKEND / "seed"


def pdf_full_text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def check_split(data: dict) -> None:
    """上架檢查（設計 5.4）：切段不合格就讓建檔失敗，強制人工檢查；例外列在 seed/split_whitelist.json。"""
    whitelist = json.loads((SEED / "split_whitelist.json").read_text(encoding="utf-8"))
    problems = split_problems(split_judgment(data["full_text"]), data["full_text"], data["citation"] in whitelist)
    if problems:
        raise SystemExit(f"切段檢查不通過：{data['citation']} {problems}（確認是例外才列入 split_whitelist.json 並寫明原因）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--citation", required=True)
    ap.add_argument("--court", required=True)
    ap.add_argument("--court-level", required=True, choices=["summary", "district", "high", "supreme"])
    ap.add_argument("--decided-on", required=True)
    ap.add_argument("--url", required=True)
    # 未精選的判決不加權（搜尋時 curated 有 1.5 倍加權）；預設維持精選，與既有種子相同
    ap.add_argument("--no-curated", dest="curated", action="store_false")
    # 案型標籤（可多個，逗號分隔）與註記（例如少數見解），設計 5.3
    ap.add_argument("--case-types", required=True)
    ap.add_argument("--annotation")
    a = ap.parse_args()
    case_types = [x.strip() for x in a.case_types.split(",") if x.strip()]
    if not case_types or any(x not in CASE_TYPES for x in case_types):
        raise SystemExit(f"案型代碼不正確：{a.case_types}（可用：{', '.join(CASE_TYPES)}）")
    pdf = a.pdf.resolve()
    if pdf.parent != (SEED / "raw").resolve():
        raise SystemExit("原始 PDF 必須放在 seed/raw/")
    data = {"citation": a.citation, "court": a.court, "court_level": a.court_level,
            "decided_on": a.decided_on, "source_url": a.url, "curated": a.curated,
            "raw_file": pdf.name, "full_text": pdf_full_text(pdf), "case_types": case_types}
    if a.annotation:
        data["annotation"] = a.annotation
    check_split(data)
    out = SEED / "judgments" / f"{pdf.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", out.name, len(data["full_text"]), "chars")


if __name__ == "__main__":
    main()
