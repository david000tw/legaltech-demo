"""下載全國法規資料庫的單條條文頁到 seed/raw/statutes，再由程式解析產生 seed/statutes/*.json。

uv run python -X utf8 scripts/build_seed_statutes.py --download   # 重新下載原始頁
uv run python -X utf8 scripts/build_seed_statutes.py              # 只從既有原始頁重建 JSON

spike（2026-11-23）：新增的條文改取自全國法規資料庫開放資料 API（https://law.moj.gov.tw/api/Ch/Law/JSON，
官方 ChLaw.json 壓縮檔）。只把用到的條文物件原樣存到 seed/raw/statutes/moj_api/{pcode}.json：
uv run python -X utf8 scripts/build_seed_statutes.py --extract-api 路徑/ChLaw.json   # 從官方檔抽出原始快照
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
# 誠實表明是工具程式，不偽裝成瀏覽器；網站若拒絕，改用全國法規資料庫的官方開放資料，不要繞過防護。
USER_AGENT = "legaltech-seed-builder/0.1"

# (法規名稱, pcode, 條號)；pcode 已於 2026-11-23 以全國法規資料庫查證。
STATUTES = [
    ("民法", "B0000001", ["184", "191-2", "193", "195", "196", "213", "215", "216", "217"]),
    ("道路交通安全規則", "K0040013", ["93", "102"]),
    ("強制汽車責任保險法", "G0390060", ["32"]),
]
API_RAW = RAW / "moj_api"
# spike：外遇（侵害配偶權）、分割遺產／特留分、借名登記三類需要的條文（開放資料 API 快照，UpdateDate 2026/11/13）
API_STATUTES = [
    ("民法", "B0000001", ["71", "87", "125", "128", "179", "185", "197", "276", "541", "549", "550", "824", "830",
                          "1138", "1141", "1144", "1146", "1148", "1150", "1151", "1164", "1165", "1173", "1187",
                          "1223", "1225"]),
    ("民事訴訟法", "B0010001", ["277"]),
    ("家事事件法", "B0010048", ["3", "51"]),
    ("保險法", "G0390002", ["112"]),
]


def _api_no(no: str) -> str:
    return f"第 {no} 條"


def api_article_text(content: str) -> str:
    """開放資料的 ArticleContent 以 CRLF 分項；轉成與單條條文頁解析結果相同的格式（逐項去頭尾空白、換行分隔）。"""
    return "\n".join(x.strip() for x in content.splitlines() if x.strip())


def extract_api(chlaw: Path) -> None:
    d = json.loads(chlaw.read_text(encoding="utf-8-sig"))
    laws = {law["LawName"]: law for law in d["Laws"]}
    API_RAW.mkdir(parents=True, exist_ok=True)
    for name, pcode, articles in API_STATUTES:
        law = laws[name]
        if not law["LawURL"].endswith(f"pcode={pcode}"):
            raise SystemExit(f"pcode 不符：{name} {law['LawURL']}")
        by_no = {a["ArticleNo"]: a for a in law["LawArticles"]}
        keep = {k: law[k] for k in ("LawName", "LawURL", "LawModifiedDate", "LawEffectiveDate")}
        keep.update({"source": "https://law.moj.gov.tw/api/Ch/Law/JSON", "UpdateDate": d["UpdateDate"],
                     "LawArticles": [by_no[_api_no(no)] for no in articles]})
        (API_RAW / f"{pcode}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
        print("extracted", pcode, len(articles), "articles")


def api_articles(pcode: str) -> dict[str, str]:
    """回傳 {條號: 條文}，條文已轉成種子格式。"""
    path = API_RAW / f"{pcode}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {a["ArticleNo"].removeprefix("第 ").removesuffix(" 條"): api_article_text(a["ArticleContent"])
            for a in raw["LawArticles"]}


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for _, pcode, articles in STATUTES:
        for no in articles:
            url = URL + urllib.parse.urlencode({"pcode": pcode, "flno": no})
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            data = urllib.request.urlopen(req, timeout=30).read()
            (RAW / f"{pcode}_{no}.html").write_bytes(data)
            print("downloaded", pcode, no, len(data))
            time.sleep(0.5)


def build() -> None:
    out_dir = SEED / "statutes"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_list = {pcode: (name, articles) for name, pcode, articles in STATUTES}
    api_list = {pcode: (name, articles) for name, pcode, articles in API_STATUTES}
    for pcode in list(dict.fromkeys([*html_list, *api_list])):
        name = (html_list.get(pcode) or api_list[pcode])[0]
        arts = []
        for no in html_list.get(pcode, (name, []))[1]:
            parsed = parse_law_single((RAW / f"{pcode}_{no}.html").read_text(encoding="utf-8"))
            if parsed["name"] != name or parsed["article_no"] != no:
                raise SystemExit(f"原始頁內容不符：{pcode}_{no} → {parsed['name']} {parsed['article_no']}")
            arts.append({"article_no": no, "text": parsed["text"], "effective_from": None})
        api = api_articles(pcode)
        for no in api_list.get(pcode, (name, []))[1]:
            if no not in api:
                raise SystemExit(f"開放資料快照缺條文：{pcode} 第{no}條（先跑 --extract-api）")
            arts.append({"article_no": no, "text": api[no], "effective_from": None})
        data = {"name": name, "pcode": pcode, "articles": arts}
        (out_dir / f"{pcode}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", pcode, len(arts), "articles")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--extract-api", type=Path, help="官方 ChLaw.json（https://law.moj.gov.tw/api/Ch/Law/JSON 解壓後）")
    a = ap.parse_args()
    if a.download:
        download()
    if a.extract_api:
        extract_api(a.extract_api)
    build()
