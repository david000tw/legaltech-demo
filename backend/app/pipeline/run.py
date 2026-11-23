"""輪次執行：拆主張 → 候選 → 對應 → 起草。失敗時另開交易寫入 failed，重跑前清掉上次產物。"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.ai.provider import AIError, AIProvider, ProviderNotAllowed, ProviderNotEnabled, get_provider
from app.config import Settings
from app.db import tenant_session
from app.models import Candidate, Claim, Draft, Firm, Round
from app.pipeline.candidates import build_candidates, select_for_claims
from app.pipeline.claims import extract_claims, opponent_pages
from app.pipeline.drafting import draft_round

log = logging.getLogger(__name__)
STALE_AFTER = timedelta(minutes=30)
NO_TEXT = "no_text: 對造書狀沒有可讀文字（可能是掃描檔），請改上傳文字檔，或等計畫 2 的辨識功能"


class RoundBusy(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def start_round(s: Session, round_id: UUID) -> None:
    rnd = s.get(Round, round_id)
    stale = rnd.status == "running" and (rnd.started_at is None or _now() - rnd.started_at > STALE_AFTER)
    if rnd.status == "drafted":
        raise RoundBusy("這一輪已完成起草，請開新的一輪")
    if rnd.status == "running" and not stale:
        raise RoundBusy("這一輪正在執行中")
    for model in (Draft, Claim, Candidate):
        s.execute(delete(model).where(model.round_id == round_id))
    rnd.status, rnd.started_at, rnd.error = "running", _now(), None
    s.commit()


def _mark_failed(firm_id: UUID, round_id: UUID, error: str) -> None:
    with tenant_session(firm_id) as s:
        rnd = s.get(Round, round_id)
        rnd.status, rnd.error = "failed", error[:1000]


class _NoText(Exception):
    pass


def run_round(firm_id: UUID, round_id: UUID, member_id: UUID | None, settings: Settings,
              provider: AIProvider | None = None,
              provider_factory: Callable[[Settings, Firm], AIProvider] = get_provider) -> None:
    try:
        with tenant_session(firm_id) as s:
            if not any(op.text is not None and op.text.text.strip() for op in opponent_pages(s, round_id)):
                raise _NoText()
            if provider is None:
                provider = provider_factory(settings, s.get(Firm, firm_id))
            extract_claims(s, round_id, provider, member_id)
            s.commit()
            build_candidates(s, round_id)
            s.commit()
            select_for_claims(s, round_id, provider, member_id)
            s.commit()
            draft_round(s, round_id, provider, member_id)
            rnd = s.get(Round, round_id)
            rnd.status, rnd.error = "drafted", None
    except _NoText:
        _mark_failed(firm_id, round_id, NO_TEXT)
    except AIError as e:
        _mark_failed(firm_id, round_id, f"{e.kind}: {e.message}")
    except (ProviderNotAllowed, ProviderNotEnabled) as e:
        _mark_failed(firm_id, round_id, f"provider: {e}")
    except Exception:
        log.exception("run_round failed (round %s)", round_id)
        _mark_failed(firm_id, round_id, "other: 系統內部錯誤，請查看伺服器紀錄後重跑")
