"""計畫 2A 驗收（設計 8.1）：30 頁合成掃描頁，透過真正的上傳 API 與辨識工作
（開發者本機的 Claude Code＋已安裝的校對員）跑完，檢查通過條件並寫進 docs/acceptance-plan-2a.md。

會用訂閱額度（約 30 頁），預設不跑：
  LEGALTECH_TEST_DB=legaltech_test_impl PYTHONUTF8=1 uv run pytest -q -s -m real_ai tests/ocr_acceptance
先檢查流程（不花額度）：加 OCR_ACCEPTANCE_DRY=1，改用假的 AI 回標準答案、不跑校對員，報告不寫入 docs。
重播（不花額度）：加 OCR_ACCEPTANCE_REPLAY=1，假的 AI 依序回上次真實驗收存下的 AI 文字、校對員照跑，
用目前的比對規則重算提醒；結果寫到 ocr_acceptance_2a_replay.json，不覆蓋真實驗收的報告（秒數、token 無意義）。
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app import db as dbmod
from app.db import tenant_session
from app.ingest.service import current_page_text
from app.main import create_app
from app.models import AIJob, Document, Page, UsageRecord
from app.ocr.alerts import alert_color
from app.ocr.state import UNREADABLE
from tests.conftest import make_settings
from tests.helpers import create_firm, create_member, login
from tests.ocr_acceptance.gen_pages import generate
from tests.pdf_helpers import image_pdf_with_text_layer

pytestmark = pytest.mark.real_ai

APP = Path(__file__).resolve().parents[3]
PADDLE = [str(APP / "tools/paddle/.venv/Scripts/python.exe"), str(APP / "tools/paddle/paddle_proofread.py")]
REPORT = APP / "docs/acceptance-plan-2a.md"
RAW = APP / "docs/acceptance/ocr_acceptance_2a.json"
RAW_REPLAY = APP / "docs/acceptance/ocr_acceptance_2a_replay.json"
HANDWRITING = APP / "docs/acceptance/handwriting"
CHECKBOX = "□■☑☐"
AUTO_START, AUTO_END = "<!-- auto:start -->", "<!-- auto:end -->"
CER_LIMIT = 0.02
AMOUNT_OR_DATE = re.compile(r"\d[\d,]*元|\d{1,3}(?:,\d{3})+|\d+年|\d+月|\d+日|\d+時|\d+分")


# ---- 正規化與比對 ----

def _keep(ch: str) -> bool:
    return not ch.isspace()


def norm_chars(text: str) -> tuple[str, list[int]]:
    """NFKC、去掉空白，「〔?〕」與「□」（標準答案裡看不出來的字）都當成一個「□」；
    回傳正規化後的字串與每個字在原文的位置。"""
    text = text.replace(UNREADABLE, "\x00\x00□")  # 長度不變，位置對得回原文
    out, pos = [], []
    for i, ch in enumerate(text):
        if ch == "\x00":
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if _keep(c):
                out.append(c)
                pos.append(i)
    return "".join(out), pos


def body(ai_text: str) -> str:
    """去掉印章行（只算內文）。"""
    return "\n".join(line for line in ai_text.splitlines() if not line.lstrip().startswith("【印章】"))


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def ai_span(truth_n: str, ai_n: str, ai_pos: list[int], start: int, end: int) -> tuple[int, int] | None:
    """標準答案 [start, end) 在辨識結果原文上大約的位置。"""
    hits = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, truth_n, ai_n, autojunk=False).get_opcodes():
        lo, hi = max(i1, start), min(i2, end)
        if lo >= hi and not (tag == "insert" and start <= i1 <= end):
            continue
        if tag == "equal":
            hits += [j1 + (k - i1) for k in range(lo, hi)]
        else:
            hits += list(range(j1, max(j2, j1 + 1)))
    hits = [h for h in hits if 0 <= h < len(ai_pos)]
    if not hits:
        return None
    return ai_pos[min(hits)], ai_pos[max(hits)] + 1


def overlaps(alerts: list[dict], span: tuple[int, int]) -> bool:
    s, e = span
    return any(a["start"] <= e and a["end"] >= s for a in alerts if a.get("kind") not in ("proofreader_missing",))


# ---- 上傳 ----

def pngs_to_pdf(paths: list[Path]) -> bytes:
    imgs = [Image.open(p).convert("RGB") for p in paths]
    buf = io.BytesIO()
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=150)
    return buf.getvalue()


def chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def trap_result(pattern: str, ai_plain: str) -> tuple[bool, str]:
    """陷阱有沒有照抄或標成看不清楚，以及報告要摘錄的那一段（比對到就摘比對到的位置）。"""
    m = re.search(pattern, ai_plain)
    if m:
        at, end = m.start(), m.end()
    else:
        at = ai_plain.find(re.sub(r"\(\?:|[()?|+\\]", "", pattern)[:4])
        end = at + 14
    near = ai_plain[max(0, at - 4):end + 4] if at >= 0 else ai_plain[:20]
    return m is not None, near.replace("|", "｜")


def upload(client, case_id, files: list[tuple[str, bytes]]) -> dict:
    field = "file" if len(files) == 1 and not files[0][0].lower().endswith((".jpg", ".png")) else "files"
    r = client.post(f"/api/cases/{case_id}/documents", data={"kind": "evidence"},
                    files=[(field, f) for f in files])
    assert r.status_code == 200, r.text
    return r.json()


# ---- 報告 ----

def write_report(summary: dict, pages: list[dict], traps: list[dict], handwriting: list[dict]) -> None:
    pct = lambda v: f"{v * 100:.2f}%"  # noqa: E731
    lines = [AUTO_START, "", f"執行時間：{summary['ran_at']}　模型：{summary['model']}　校對員："
             f"{'已安裝' if summary['proofreader'] else '未安裝'}", "",
             "### 通過條件", "", "| 項目 | 結果 | 通過條件 | 判定 |", "|---|---|---|---|",
             f"| 辨識頁數 | {summary['pages']} | ≥30 | {'通過' if summary['pages'] >= 30 else '未通過'} |",
             f"| 內文錯字率（去掉印章行） | {pct(summary['cer'])} | ≤2% | {'通過' if summary['cer'] <= CER_LIMIT else '未通過'} |",
             f"| 陷阱照抄或標成看不清楚 | {summary['traps_ok']}／{summary['traps']} | 全部 | "
             f"{'通過' if summary['traps_ok'] == summary['traps'] else '未通過'} |",
             f"| 讀錯的金額、日期都有提醒 | 讀錯 {summary['misread']} 處，未標 {summary['unflagged']} 處 | 未標 0 | "
             f"{'通過' if summary['unflagged'] == 0 else '未通過'} |",
             f"| 勾選框沒有被標成看不清楚 | {summary['checkbox_bad']} 處 | 0 | {'通過' if summary['checkbox_bad'] == 0 else '未通過'} |",
             f"| 每頁平均黃色提醒（比對不一致） | {summary['yellow_per_page']:.2f}（其中校對員不一致 {summary['mismatch_per_page']:.2f}） | 記錄 | — |",
             f"| 每頁秒數 | {summary['seconds_per_page']:.1f} | 記錄（畫面預估 20） | — |",
             f"| 每頁 token | {summary['tokens_per_page']:.0f} | 記錄 | — |", "",
             "### 每頁結果", "", "| 頁 | 情況 | 錯字率 | 紅色提醒 | 黃色提醒 | 讀錯未標 |", "|---|---|---|---|---|---|"]
    lines += [f"| {p['id']} | {p['variant']} | {pct(p['cer'])} | {p['red']} | {p['yellow']} | "
              f"{'、'.join(p['unflagged']) or '—'} |" for p in pages]
    lines += ["", "### 陷阱", "", "| 頁 | 陷阱 | 結果 | 辨識結果（摘錄） |", "|---|---|---|---|"]
    lines += [f"| {t['page']} | {t['name']} | {'照抄／標成看不清楚' if t['ok'] else '未通過'} | {t['excerpt']} |"
              for t in traps]
    lines += ["", "### 手寫", ""]
    if handwriting:
        lines += ["| 照片 | 錯字率 |", "|---|---|"]
        lines += [f"| {h['name']} | {pct(h['cer']) if h['cer'] is not None else '沒有標準答案'} |" for h in handwriting]
    else:
        lines.append("手寫未測（`docs/acceptance/handwriting/` 沒有照片）。")
    lines += ["", AUTO_END]
    block = "\n".join(lines)
    if REPORT.exists():
        old = REPORT.read_text(encoding="utf-8")
        if AUTO_START in old and AUTO_END in old:
            new = old[:old.index(AUTO_START)] + block + old[old.index(AUTO_END) + len(AUTO_END):]
        else:
            new = old.rstrip() + "\n\n" + block + "\n"
    else:
        new = ("# 計畫 2A 驗收紀錄：掃描辨識與原圖對照\n\n"
               "依設計第 8.1 節。自動驗收由 `tests/ocr_acceptance/test_ocr_acceptance.py` 產生下面這一段；"
               "瀏覽器實測寫在後面。\n\n## 自動驗收（30 頁合成掃描頁）\n\n" + block + "\n")
    REPORT.write_text(new, encoding="utf-8")


# ---- 驗收 ----

def test_ocr_acceptance(db, tmp_path):
    dry = os.environ.get("OCR_ACCEPTANCE_DRY") == "1"
    replay = os.environ.get("OCR_ACCEPTANCE_REPLAY") == "1" and not dry
    paddle = PADDLE if Path(PADDLE[0]).exists() and not dry else []
    settings = make_settings(tmp_path, paddle_cmd=paddle)
    dbmod.configure(settings)
    firm = create_firm("驗收事務所", internal=True)
    create_member(firm, "lawyer@accept.test")
    client = TestClient(create_app(settings))
    assert login(client, "lawyer@accept.test").status_code == 200
    case_id = client.post("/api/cases", json={"title": "林大明車禍案（驗收）", "our_side": "plaintiff"}).json()["id"]

    truth = generate(tmp_path / "set")
    if dry or replay:  # 依上傳順序回答案：檢查流程回標準答案（看不出來的那一位寫成〔?〕）；重播回上次真實的 AI 文字
        from tests.fakes.fake_provider import FakeProvider
        order = [t for kind in ("scan", "phone", "photos", "textpdf") for t in truth if t["upload"] == kind]
        if replay:
            saved = {p["id"]: p["text"] for p in json.loads(RAW.read_text(encoding="utf-8"))["pages"]}
            answers = iter(saved[t["id"]] for t in order)
        else:
            answers = iter(t["text"].replace("1□8", f"1{UNREADABLE}8") for t in order)
        fake = FakeProvider({"ocr": lambda call: {"text": next(answers)}})
        client.app.state.provider_factory = lambda st, f: fake
    pages_dir = tmp_path / "set/pages"
    groups: list[tuple[str, list[str]]] = []  # (document_id, 依頁碼的標準答案 id)
    started = time.monotonic()
    scans = [t for t in truth if t["upload"] == "scan"]
    for n, chunk in enumerate(chunks(scans, 5), start=1):
        doc = upload(client, case_id, [(f"掃描{n:02d}.pdf", pngs_to_pdf([pages_dir / f"{t['id']}.png" for t in chunk]))])
        groups.append((doc["id"], [t["id"] for t in chunk]))
    for kind in ("phone", "photos"):
        items = [t for t in truth if t["upload"] == kind]
        doc = upload(client, case_id, [(f"{t['id']}.jpg", (pages_dir / f"{t['id']}.jpg").read_bytes()) for t in items])
        groups.append((doc["id"], [t["id"] for t in items]))
    tl = next(t for t in truth if t["upload"] == "textpdf")
    pdf = image_pdf_with_text_layer((pages_dir / f"{tl['id']}.png").read_bytes(), "Medical certificate scan, OCR text layer")
    doc = upload(client, case_id, [("掃描加文字層.pdf", pdf)])
    detail = client.get(f"/api/cases/{case_id}").json()
    tl_doc = next(d for d in detail["documents"] if d["id"] == doc["id"])
    assert tl_doc["ocr"]["scan_hint"] is True and tl_doc["ocr"]["can_reocr"] is True
    r = client.post(f"/api/documents/{doc['id']}/reocr", json={})
    assert r.status_code == 200, r.text
    groups.append((doc["id"], [tl["id"]]))
    handwriting_files = sorted(p for p in HANDWRITING.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")) \
        if HANDWRITING.exists() else []
    hw_docs = [(p, upload(client, case_id, [(p.name, p.read_bytes())])["id"]) for p in handwriting_files]
    elapsed = time.monotonic() - started

    by_id = {t["id"]: t for t in truth}
    page_rows, trap_rows, errs, total = [], [], 0, 0
    misread = unflagged = checkbox_bad = 0
    yellow = mismatch = 0
    with tenant_session(firm) as s:
        for doc_id, ids in groups:
            pages = list(s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)))
            assert len(pages) == len(ids), (doc_id, len(pages), ids)
            for page, tid in zip(pages, ids):
                t = by_id[tid]
                pt = current_page_text(s, page.id)
                assert page.ocr_state == "done" and pt is not None and pt.source == "ai_ocr", (tid, page.ocr_state)
                ai, alerts = pt.text, pt.alerts or []
                truth_n, _ = norm_chars(t["text"])
                ai_body_n, _ = norm_chars(body(ai))
                e = lev(truth_n, ai_body_n)
                errs, total = errs + e, total + len(truth_n)
                ai_n, ai_pos = norm_chars(ai)
                bad = []
                for crit in t["critical"]:
                    c_n, _ = norm_chars(crit)
                    if not AMOUNT_OR_DATE.search(c_n) or c_n in ai_n:
                        continue
                    misread += 1
                    at = truth_n.find(c_n)
                    span = ai_span(truth_n, ai_n, ai_pos, at, at + len(c_n)) if at >= 0 else None
                    if span is None or not overlaps(alerts, span):
                        unflagged += 1
                        bad.append(crit)
                # 陷阱比對用原樣（保留「〔?〕」），只做 NFKC、去空白
                ai_plain = "".join(c for c in unicodedata.normalize("NFKC", ai) if not c.isspace())
                for trap in t["traps"]:
                    ok, near = trap_result(trap["pattern"], ai_plain)
                    trap_rows.append({"page": tid, "name": trap["name"], "ok": ok, "excerpt": near})
                if t["checkbox"]:
                    for line in ai.splitlines():
                        if any(c in line for c in CHECKBOX) and UNREADABLE in line:
                            checkbox_bad += 1  # 勾選框那一行被標成看不清楚：引用時會 quote_unreadable
                    checkbox_bad += sum(1 for a in alerts if a["kind"] == "unreadable"
                                        and any(c in ai[a["start"]:a["end"]] for c in CHECKBOX))
                y = [a for a in alerts if alert_color(a["kind"]) == "yellow"]
                yellow += len(y)
                mismatch += sum(1 for a in y if a["kind"] == "mismatch")
                page_rows.append({"id": tid, "variant": t["variant"], "cer": e / max(len(truth_n), 1),
                                  "red": len(alerts) - len(y), "yellow": len(y), "unflagged": bad, "text": ai,
                                  "alerts": alerts})
        hw_rows = []
        for path, doc_id in hw_docs:
            pg = s.scalars(select(Page).where(Page.document_id == doc_id)).first()
            pt = current_page_text(s, pg.id)
            ref = path.with_suffix(".txt")
            cer = None
            if ref.exists() and pt is not None:
                tn, _ = norm_chars(ref.read_text(encoding="utf-8"))
                cer = lev(tn, norm_chars(body(pt.text))[0]) / max(len(tn), 1)
            hw_rows.append({"name": path.name, "cer": cer})
        tokens = s.scalar(select(func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens
                                                        + UsageRecord.cache_read_tokens + UsageRecord.cache_write_tokens), 0))
                          .where(UsageRecord.step == "ocr")) or 0
        jobs = list(s.scalars(select(AIJob).where(AIJob.step == "ocr", AIJob.status == "done")))
        job_seconds = sum((j.finished_at - j.started_at).total_seconds() for j in jobs if j.finished_at)

    n = len(page_rows)
    summary = {"ran_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
               "model": settings.ocr_model, "proofreader": bool(paddle), "pages": n, "cer": errs / max(total, 1),
               "traps": len(trap_rows), "traps_ok": sum(1 for t in trap_rows if t["ok"]), "misread": misread,
               "unflagged": unflagged, "checkbox_bad": checkbox_bad, "yellow_per_page": yellow / max(n, 1),
               "mismatch_per_page": mismatch / max(n, 1), "seconds_per_page": (job_seconds or elapsed) / max(n, 1),
               "tokens_per_page": tokens / max(n, 1)}
    if replay:
        summary["replay_of"] = json.loads(RAW.read_text(encoding="utf-8"))["summary"]["ran_at"]
        RAW_REPLAY.write_text(json.dumps({"summary": summary, "pages": page_rows}, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    elif not dry:
        write_report(summary, page_rows, trap_rows, hw_rows)
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(json.dumps({"summary": summary, "pages": page_rows, "traps": trap_rows}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
    print("\n", json.dumps(summary, ensure_ascii=False, indent=1))

    assert n >= 30
    assert summary["cer"] <= CER_LIMIT, summary
    assert summary["traps_ok"] == summary["traps"], [t for t in trap_rows if not t["ok"]]
    assert unflagged == 0, [(p["id"], p["unflagged"]) for p in page_rows if p["unflagged"]]
    assert checkbox_bad == 0
