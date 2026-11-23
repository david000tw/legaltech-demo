from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.ai.provider import AIError, ProviderNotAllowed, ProviderNotEnabled
from app.auth import CurrentMember, current_member
from app.db import tenant_session
from app.models import Claim, Draft, Firm, Page, Paragraph, Round
from app.pipeline.drafting import rewrite_paragraph
from app.pipeline.run import STALE_AFTER, RoundBusy, run_round, start_round

router = APIRouter(prefix="/api")


@router.post("/rounds/{round_id}/run", status_code=202)
def run(round_id: UUID, request: Request, background: BackgroundTasks,
        m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        if s.get(Round, round_id) is None:
            raise HTTPException(404, "找不到輪次")
        try:
            start_round(s, round_id)
        except RoundBusy as e:
            raise HTTPException(409, str(e))
    background.add_task(run_round, m.firm_id, round_id, m.id, request.app.state.settings,
                        provider_factory=request.app.state.provider_factory)
    return {"status": "running"}


@router.get("/rounds/{round_id}")
def get_round(round_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        rnd = s.get(Round, round_id)
        if rnd is None:
            raise HTTPException(404, "找不到輪次")
        claims = s.scalars(select(Claim).where(Claim.round_id == round_id).order_by(Claim.seq)).all()
        draft = s.scalars(select(Draft).where(Draft.round_id == round_id).order_by(Draft.created_at.desc())).first()
        stale = rnd.status == "running" and rnd.started_at is not None and (
            datetime.now(timezone.utc) - rnd.started_at > STALE_AFTER)
        return {
            "id": str(rnd.id), "seq": rnd.seq, "status": rnd.status, "error": rnd.error, "stale": stale,
            "draft_id": str(draft.id) if draft else None,
            "claims": [{"seq": c.seq, "summary": c.summary, "quote": c.quote, "quote_verified": c.quote_verified,
                        "page_no": s.get(Page, c.page_id).page_no if c.page_id else None} for c in claims],
        }


class RewriteBody(BaseModel):
    instruction: str


@router.post("/paragraphs/{paragraph_id}/rewrite")
def rewrite(paragraph_id: UUID, body: RewriteBody, request: Request,
            m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        if s.get(Paragraph, paragraph_id) is None:
            raise HTTPException(404, "找不到段落")
        try:
            provider = request.app.state.provider_factory(request.app.state.settings, s.get(Firm, m.firm_id))
            p = rewrite_paragraph(s, paragraph_id, body.instruction, provider, m.id)
        except (ProviderNotAllowed, ProviderNotEnabled) as e:
            raise HTTPException(403, str(e))
        except AIError as e:
            raise HTTPException(503, f"{e.kind}: {e.message}")
        return {"id": str(p.id), "status": p.status, "text": p.text, "verify_reasons": p.verify_reasons}
