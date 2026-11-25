"""支持度檢查的背景工作（設計 7.1、7.2）。

- 開始時以隨機 claim_token 認領還沒人認領的列：同時有兩個工作時，同一列只會送一次 AI。
- 依原文排序後每批最多 SUPPORT_BATCH 筆；同一批同一段原文只送一次。
- 每批送出前更新自己所有還沒處理完的列的 claimed_at（心跳），排在後面批次的列不會被誤判卡住。
- 每批在獨立的短交易裡讀與寫；呼叫 AI 時不開交易（用量由 call_ai 另開交易記錄）。
- 寫入一律限 status='pending' 且 claim_token 相符，不覆蓋已被接手或已寫好的列。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import groupby
from uuid import UUID

from sqlalchemy import select, update

from app.ai.prompts import load_prompt
from app.ai.provider import AIError, AIProvider, ProviderNotAllowed, ProviderNotEnabled, get_provider
from app.ai.schemas import support_schema
from app.ai.usage import call_ai
from app.config import Settings
from app.db import tenant_session
from app.models import SUPPORT_VERDICTS, Firm, SupportCheck
from app.support.service import SUPPORT_TIMEOUT_S
from app.text_norm import find_quote

log = logging.getLogger(__name__)

SUPPORT_BATCH = 8
SOURCE_WINDOW = 2000  # 字
# 判決段落整段送（系統內最長約 7,100 字）：「被告辯稱……」可能寫在離引文很遠的前面，截掉就看不出是當事人主張
JUDGMENT_WINDOW = 8000
KIND_ZH = {"evidence": "卷證", "judgment": "判決", "statute": "法條"}
# 之後的批次也不會成功，剩下的直接記失敗。逾時也算：AI 卡住時若一批一批各等 240 秒，
# 後面的引用會「檢查中」好幾十分鐘、律師不能放行（設計 7.3）；記成尚未檢查後按一次「重新檢查」即可
STOP_KINDS = ("usage_limit", "not_logged_in", "timeout")
MISSING = "invalid_output: 缺少結果"
OTHER = "other: 系統內部錯誤"


@dataclass(frozen=True)
class _Item:
    id: UUID
    case_id: UUID
    kind: str
    label: str
    quote: str
    sentence: str
    source: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def source_excerpt(source: str, quote: str, limit: int = SOURCE_WINDOW) -> str:
    """原文不超過 limit 時回全文；超過時以引文位置為中心截取 limit 字（找不到引文就取開頭）。"""
    if len(source) <= limit:
        return source
    pos = find_quote(source, quote) if quote else None
    if pos is None:
        begin = 0
    else:
        center = (pos[0] + pos[1]) // 2
        begin = max(0, min(center - limit // 2, len(source) - limit))
    end = begin + limit
    return ("……" if begin > 0 else "") + source[begin:end] + ("……" if end < len(source) else "")


def _claim(firm_id: UUID, check_ids: list[UUID], token: UUID) -> list[_Item]:
    with tenant_session(firm_id) as s:
        rows = list(s.scalars(
            select(SupportCheck).where(SupportCheck.id.in_(check_ids), SupportCheck.status == "pending",
                                       SupportCheck.claim_token.is_(None))
            .order_by(SupportCheck.id).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)))
        now = _now()
        for r in rows:
            r.claim_token, r.claimed_at = token, now
        return [_Item(r.id, r.case_id, r.kind, r.label, r.quote, r.sentence, r.source) for r in rows]


def _beat(firm_id: UUID, token: UUID, remaining: list[UUID], batch: list[UUID]) -> set[UUID]:
    """心跳：自己還沒處理完的列都更新 claimed_at；回傳這一批仍屬於自己的列。"""
    with tenant_session(firm_id) as s:
        mine = list(s.scalars(
            select(SupportCheck.id).where(SupportCheck.id.in_(remaining), SupportCheck.claim_token == token,
                                          SupportCheck.status == "pending")
            .order_by(SupportCheck.id).with_for_update()))
        if mine:
            s.execute(update(SupportCheck).where(SupportCheck.id.in_(mine)).values(claimed_at=_now()))
        return set(mine) & set(batch)


def _fail(firm_id: UUID, token: UUID, ids: list[UUID], error: str) -> None:
    if not ids:
        return
    with tenant_session(firm_id) as s:
        for rid in sorted(ids):
            s.execute(update(SupportCheck).where(SupportCheck.id == rid, SupportCheck.status == "pending",
                                                 SupportCheck.claim_token == token)
                      .values(status="failed", error=error, claim_token=None, claimed_at=None))


def _write(firm_id: UUID, token: UUID, codes: dict[str, _Item], data: dict) -> None:
    answers: dict[str, tuple[str, str]] = {}
    for x in (data or {}).get("items") or []:
        if (isinstance(x, dict) and isinstance(x.get("id"), str) and x["id"] in codes
                and x.get("verdict") in SUPPORT_VERDICTS):
            answers.setdefault(x["id"], (x["verdict"], str(x.get("explanation") or "")))
    now = _now()
    with tenant_session(firm_id) as s:
        for code, item in sorted(codes.items(), key=lambda kv: kv[1].id):
            mine = (SupportCheck.id == item.id, SupportCheck.status == "pending", SupportCheck.claim_token == token)
            if code not in answers:
                s.execute(update(SupportCheck).where(*mine)
                          .values(status="failed", error=MISSING, claim_token=None, claimed_at=None))
                continue
            verdict, explanation = answers[code]
            if verdict == "party_claim_misuse" and item.kind != "judgment":
                verdict = "not_supported"  # 「誤用當事人主張」只適用判決
            s.execute(update(SupportCheck).where(*mine).values(
                status="done", verdict=verdict, explanation=explanation[:300], error=None, checked_at=now))


def _user_prompt(items: list[_Item]) -> tuple[dict[str, _Item], str]:
    """同一段原文（同種類、同摘錄）只列一次；每筆引用標明用哪一段原文。"""
    sources: dict[tuple[str, str], str] = {}
    head: list[str] = []
    body: list[str] = []
    codes: dict[str, _Item] = {}
    for n, item in enumerate(items, start=1):
        excerpt = source_excerpt(item.source, item.quote,
                                 JUDGMENT_WINDOW if item.kind == "judgment" else SOURCE_WINDOW)
        key = (item.kind, excerpt)
        if key not in sources:
            sources[key] = f"S{len(sources) + 1}"
            head.append(f"【原文 {sources[key]}】{KIND_ZH.get(item.kind, item.kind)}：{item.label}\n{excerpt}")
        code = f"C{n}"
        codes[code] = item
        body.append(f"【引用 {code}】原文 {sources[key]}\n【引文】{item.quote or '（無）'}\n【書狀句子】{item.sentence}")
    return codes, "\n\n".join(head + body)


def _order(item: _Item) -> tuple:
    return (str(item.case_id), item.kind, hashlib.sha256(item.source.encode("utf-8")).hexdigest(), str(item.id))


def run_support_checks(firm_id: UUID, check_ids: list[UUID], settings: Settings, member_id: UUID | None = None,
                       provider: AIProvider | None = None, provider_factory=get_provider) -> None:
    if not check_ids:
        return
    token = uuid.uuid4()
    items = sorted(_claim(firm_id, check_ids, token), key=_order)
    if not items:
        return
    remaining = [i.id for i in items]
    if provider is None:
        try:
            with tenant_session(firm_id) as s:
                provider = provider_factory(settings, s.get(Firm, firm_id))
        except (ProviderNotAllowed, ProviderNotEnabled) as e:
            _fail(firm_id, token, remaining, f"provider: {e}")
            return
        except Exception:  # noqa: BLE001  認領的列不能卡在「檢查中」
            log.exception("support check provider setup failed")
            _fail(firm_id, token, remaining, OTHER)
            return
    batches: list[list[_Item]] = []
    for _, group in groupby(items, key=lambda i: i.case_id):
        group = list(group)
        batches.extend(group[k:k + SUPPORT_BATCH] for k in range(0, len(group), SUPPORT_BATCH))
    for batch in batches:
        try:
            mine = _beat(firm_id, token, remaining, [i.id for i in batch])
        except Exception:  # noqa: BLE001  資料庫出錯：認領的列當下記成尚未檢查，不卡在「檢查中」
            log.exception("support check heartbeat failed")
            _fail(firm_id, token, remaining, OTHER)
            return
        remaining = [i for i in remaining if i not in {b.id for b in batch}]
        todo = [i for i in batch if i.id in mine]
        if not todo:
            continue
        ids = [i.id for i in todo]
        try:
            codes, user = _user_prompt(todo)
            result = call_ai(provider, firm_id=firm_id, case_id=todo[0].case_id, member_id=member_id,
                             step="support", system=load_prompt("support"), user=user,
                             schema=support_schema(list(codes)), effort="medium", timeout_s=SUPPORT_TIMEOUT_S)
            _write(firm_id, token, codes, result.data)  # 寫入出錯也只讓這一批記失敗
        except AIError as e:
            _fail(firm_id, token, ids, f"{e.kind}: {e.message}")
            if e.kind in STOP_KINDS:
                _fail(firm_id, token, remaining, f"{e.kind}: {e.message}")
                return
            continue
        except (ProviderNotAllowed, ProviderNotEnabled) as e:
            _fail(firm_id, token, ids + remaining, f"provider: {e}")
            return
        except Exception:  # noqa: BLE001  一批出錯不影響其他批
            log.exception("support check batch failed")
            _fail(firm_id, token, ids, OTHER)
            continue
