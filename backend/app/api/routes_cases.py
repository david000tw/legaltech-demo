from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from app.auth import CurrentMember, current_member
from app.case_types import CASE_TYPES, DEFAULT_CASE_TYPE
from app.db import tenant_session
from app.ingest.extract import UnreadableFile, UnsupportedFile
from app.ingest.service import current_page_text, ingest_document
from app.models import Case, Document, Exhibit, Page, Round

router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class CaseBody(BaseModel):
    title: str
    our_side: Literal["plaintiff", "defendant"]
    case_type: str = DEFAULT_CASE_TYPE

    @field_validator("case_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in CASE_TYPES:
            raise ValueError(f"不支援的案型：{v}")
        return v


def _case_or_404(s, case_id: UUID) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "找不到案件")
    return case


def _case_summary(c: Case) -> dict:
    return {"id": str(c.id), "title": c.title, "our_side": c.our_side, "case_type": c.case_type}


@router.post("/cases")
def create_case(body: CaseBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        c = Case(firm_id=m.firm_id, title=body.title, our_side=body.our_side, case_type=body.case_type,
                 created_by=m.id)
        s.add(c)
        s.flush()
        return _case_summary(c)


@router.get("/cases")
def list_cases(m: CurrentMember = Depends(current_member)) -> list[dict]:
    with tenant_session(m.firm_id) as s:
        return [_case_summary(c) for c in s.scalars(select(Case).order_by(Case.created_at.desc()))]


@router.get("/cases/{case_id}")
def get_case(case_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        case = _case_or_404(s, case_id)
        docs = s.scalars(select(Document).where(Document.case_id == case_id)
                         .order_by(Document.created_at, Document.filename)).all()
        pages = s.scalars(select(Page).where(Page.document_id.in_([d.id for d in docs]))
                          .order_by(Page.page_no)).all()
        by_doc: dict[UUID, list[dict]] = {}
        for p in pages:
            by_doc.setdefault(p.document_id, []).append(
                {"id": str(p.id), "page_no": p.page_no, "needs_ocr": p.needs_ocr})
        exhibits = s.scalars(select(Exhibit).where(Exhibit.case_id == case_id)
                             .order_by(Exhibit.prefix, Exhibit.number)).all()
        names = {d.id: d.filename for d in docs}
        rounds = s.scalars(select(Round).where(Round.case_id == case_id).order_by(Round.seq)).all()
        return {
            **_case_summary(case),
            "documents": [{"id": str(d.id), "kind": d.kind, "filename": d.filename,
                           "round_id": str(d.round_id) if d.round_id else None,
                           "page_count": d.page_count, "pages": by_doc.get(d.id, [])} for d in docs],
            "exhibits": [{"id": str(e.id), "label": f"{e.prefix}{e.number}", "prefix": e.prefix,
                          "number": e.number, "document_id": str(e.document_id),
                          "filename": names.get(e.document_id)} for e in exhibits],
            "rounds": [{"id": str(r.id), "seq": r.seq, "status": r.status, "error": r.error} for r in rounds],
        }


@router.post("/cases/{case_id}/rounds")
def create_round(case_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        if s.get(Case, case_id, with_for_update=True) is None:  # 鎖住案件列，避免同時建輪次撞號
            raise HTTPException(404, "找不到案件")
        seq = (s.scalar(select(func.max(Round.seq)).where(Round.case_id == case_id)) or 0) + 1
        r = Round(firm_id=m.firm_id, case_id=case_id, seq=seq, status="created")
        s.add(r)
        s.flush()
        return {"id": str(r.id), "seq": r.seq}


@router.post("/cases/{case_id}/documents")
def upload_document(case_id: UUID, file: UploadFile,
                          kind: Literal["evidence", "opponent_brief"] = Form(...),
                          round_id: UUID | None = Form(None),
                          m: CurrentMember = Depends(current_member)) -> dict:
    # 一般 def：FastAPI 會放到執行緒池，抽字時不會卡住其他請求。
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        if round_id is not None:
            r = s.get(Round, round_id)
            if r is None or r.case_id != case_id:
                raise HTTPException(404, "找不到輪次")
        try:
            doc = ingest_document(s, m.firm_id, case_id, kind, file.filename or "未命名", data, round_id)
        except UnsupportedFile as e:
            raise HTTPException(415, str(e))
        except UnreadableFile as e:
            raise HTTPException(422, str(e))
        return {"id": str(doc.id), "page_count": doc.page_count}


@router.get("/pages/{page_id}")
def get_page(page_id: UUID, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        page = s.get(Page, page_id)
        if page is None:
            raise HTTPException(404, "找不到頁面")
        pt = current_page_text(s, page_id)
        return {"id": str(page.id), "page_no": page.page_no, "needs_ocr": page.needs_ocr,
                "text": pt.text if pt else None, "version": pt.version if pt else None}
