"""判決要旨（節錄式，計畫 3 丙）：AI 只從法院理由段挑原文句子，系統逐字比對；另加 AI 寫的短爭點標籤。

- 律師或助理第一次打開整份判決時才產生（POST），做好存在共用的 judgment_summaries，所有事務所共用；
  費用記在觸發的那位成員所屬的事務所與案件。AI 只拿到判決字號與法院理由段，不放任何案件資料。
- 同一份判決同一時間只產生一次（跨事務所、跨伺服器程序）：以 judgment_summaries 那一列認領
  （INSERT … ON CONFLICT DO UPDATE … WHERE，寫入新的 claim_token），寫回一律限 claim_token 相符。
- 心跳：只有一次 AI 呼叫，認領時寫入的 claimed_at 就是心跳；SUMMARY_STALE_AFTER 大於最長耗時。
- 產生後檢查一次，每次讀取時再檢查一次（共用表被改過或段落重切過，也不會把錯誤內容放進畫面或提示詞）。
- 失敗只存種類（error_kind），不存 AI 服務回傳的錯誤訊息全文。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.provider import AIError, ProviderNotAllowed, ProviderNotEnabled
from app.ai.schemas import judgment_summary_schema
from app.ai.usage import call_ai
from app.config import Settings
from app.db import app_session, corpus_session, tenant_session
from app.models import Firm, Judgment, JudgmentPassage, JudgmentSummary
from app.text_norm import find_quote, normalize_text
from app.verify.citations import _in_party_claim

log = logging.getLogger(__name__)

SUMMARY_TIMEOUT_S = 180  # 每次 AI 呼叫的逾時；Claude Code 失敗會再試一次，所以一次產生最長約 6 分鐘
SUMMARY_STALE_AFTER = timedelta(minutes=9)  # 必須大於 2 × SUMMARY_TIMEOUT_S 再加 2 分鐘；從 claimed_at 算
MAX_ISSUES, MAX_EXCERPTS = 3, 4
EXCERPT_MIN, EXCERPT_MAX = 10, 200  # 正規化後的字數
_TAG = re.compile(r"^[一-鿿0-9A-Za-z、]{1,15}$")
FAILED = "要旨產生失敗"
NO_REASONING = "這份判決沒有法院理由段，無法產生要旨"
UNAVAILABLE = "要旨暫時無法產生，請聯絡系統管理員"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reasoning_passages(s: Session, judgment_id: UUID) -> list[JudgmentPassage]:
    return list(s.scalars(select(JudgmentPassage).where(
        JudgmentPassage.judgment_id == judgment_id, JudgmentPassage.passage_type == "court_reasoning",
        JudgmentPassage.status == "active").order_by(JudgmentPassage.seq)))


def fingerprint(passages: list[JudgmentPassage]) -> str:
    raw = json.dumps([[p.seq, p.text] for p in sorted(passages, key=lambda p: p.seq)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def validated(issues: list, excerpts: list, passages: dict[int, JudgmentPassage]) -> tuple[list[str], list[dict]]:
    """爭點標籤只留合格的（最多 3 個）；節錄句必須在它註明的那一段原文裡逐字找得到、不是當事人主張、
    長度合格（最多 4 句）。回傳的節錄句附 span（該段原文裡的位置）。"""
    tags: list[str] = []
    for t in issues if isinstance(issues, list) else []:
        if isinstance(t, str) and _TAG.match(t) and t not in tags and len(tags) < MAX_ISSUES:
            tags.append(t)
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for e in excerpts if isinstance(excerpts, list) else []:
        if not isinstance(e, dict) or len(out) >= MAX_EXCERPTS:
            continue
        seq, text = e.get("passage_seq"), e.get("text")
        p = passages.get(seq) if isinstance(seq, int) else None
        if p is None or not isinstance(text, str):
            continue
        norm = normalize_text(text)
        if not EXCERPT_MIN <= len(norm) <= EXCERPT_MAX or (seq, norm) in seen:
            continue
        span = find_quote(p.text, text)
        if span is None or _in_party_claim(p.text, text):
            continue
        seen.add((seq, norm))
        out.append({"passage_seq": seq, "text": text, "span": list(span)})
    return tags, out


def _view(status: str, issues=(), excerpts=(), message: str | None = None) -> dict:
    return {"status": status, "issues": list(issues), "excerpts": list(excerpts), "message": message}


def summary_view(s: Session, j: Judgment) -> dict:
    """status：none（還沒有）／stale（過期，打開時會重新產生）／generating／ok／failed。"""
    row = s.scalars(select(JudgmentSummary).where(JudgmentSummary.judgment_id == j.id)).one_or_none()
    if row is None:
        return _view("none")
    passages = reasoning_passages(s, j.id)
    if row.fingerprint != fingerprint(passages):
        return _view("stale")
    if row.status == "generating":
        stuck = row.claimed_at is None or row.claimed_at < _now() - SUMMARY_STALE_AFTER
        return _view("stale" if stuck else "generating")
    if row.status == "failed":
        return _view("failed", message=FAILED)
    issues, excerpts = validated(row.issues, row.excerpts, {p.seq: p for p in passages})
    return _view("ok", issues, excerpts) if excerpts else _view("stale")


def claim(j: Judgment, *, retry: bool = False, force: bool = False) -> UUID | None:
    """認領這份判決的要旨產生權；搶到回新的 claim_token，沒搶到回 None。
    失敗的要旨只在按「重試」（retry）時重新產生：打開頁面不自動重試，避免 AI 一直失敗時每次打開都花錢。"""
    token, now = uuid.uuid4(), _now()
    t = JudgmentSummary.__table__.c
    with corpus_session() as cs:
        fp = fingerprint(reasoning_passages(cs, j.id))
        conditions = [t.fingerprint != fp,
                      and_(t.status == "generating", or_(t.claimed_at.is_(None), t.claimed_at < now - SUMMARY_STALE_AFTER))]
        if retry:
            conditions.append(t.status == "failed")
        if force:
            conditions.append(t.status == "ok")
        stmt = (insert(JudgmentSummary.__table__)
                .values(id=uuid.uuid4(), judgment_id=j.id, status="generating", claim_token=token, claimed_at=now,
                        fingerprint=fp, issues=[], excerpts=[])
                .on_conflict_do_update(constraint="uq_judgment_summary_judgment",
                                       set_={"status": "generating", "claim_token": token, "claimed_at": now,
                                             "fingerprint": fp, "error_kind": None},
                                       where=or_(*conditions))
                .returning(t.claim_token))
        got = cs.execute(stmt).scalar_one_or_none()
    return token if got == token else None


def _write(judgment_id: UUID, token: UUID, **values) -> bool:
    with corpus_session() as cs:
        r = cs.execute(update(JudgmentSummary.__table__).where(
            JudgmentSummary.__table__.c.judgment_id == judgment_id, JudgmentSummary.__table__.c.claim_token == token,
            JudgmentSummary.__table__.c.status == "generating").values(finished_at=_now(), **values))
        return r.rowcount == 1


def finish_summary(judgment_id: UUID, token: UUID, *, issues: list[str], excerpts: list[dict], model: str) -> bool:
    return _write(judgment_id, token, status="ok", issues=issues, excerpts=excerpts, model=model[:100],
                  error_kind=None)


def fail_summary(judgment_id: UUID, token: UUID, kind: str) -> bool:
    return _write(judgment_id, token, status="failed", error_kind=kind[:40])


def _prompt(citation: str, passages: list[JudgmentPassage]) -> str:
    body = "\n\n".join(f"[段{p.seq}]\n{p.text}" for p in passages)
    return f"【判決】{citation}\n\n【法院理由】\n{body}"


def run_summary(judgment_id: UUID, token: UUID, firm_id: UUID, case_id: UUID, member_id: UUID, settings: Settings,
                provider_factory) -> None:
    """背景工作：讀法院理由（短連線，關掉後才呼叫 AI）→ AI → 用目前段落檢查 → 寫回（限自己的認領代號）。"""
    try:
        with app_session() as s:
            citation = s.get(Judgment, judgment_id).citation
            user = _prompt(citation, reasoning_passages(s, judgment_id))
        with tenant_session(firm_id) as s:
            provider = provider_factory(settings, s.get(Firm, firm_id))
        result = call_ai(provider, firm_id=firm_id, case_id=case_id, member_id=member_id, step="judgment_summary",
                         system=load_prompt("judgment_summary"), user=user, schema=judgment_summary_schema(),
                         effort="low", timeout_s=SUMMARY_TIMEOUT_S)
        with app_session() as s:
            current = {p.seq: p for p in reasoning_passages(s, judgment_id)}
            issues, excerpts = validated(result.data.get("issues"), result.data.get("excerpts"), current)
        if excerpts:
            finish_summary(judgment_id, token, issues=issues, model=result.model,
                           excerpts=[{"passage_seq": e["passage_seq"], "text": e["text"]} for e in excerpts])
        else:
            fail_summary(judgment_id, token, "no_valid_excerpt")
    except AIError as e:
        fail_summary(judgment_id, token, e.kind)
    except (ProviderNotAllowed, ProviderNotEnabled):
        fail_summary(judgment_id, token, "provider")
    except Exception:  # noqa: BLE001  認領的列不能卡在「產生中」
        log.exception("judgment summary failed")
        try:
            fail_summary(judgment_id, token, "other")
        except Exception:  # noqa: BLE001
            log.exception("judgment summary: could not record failure")
