from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import func, select

from app.auth import CurrentMember, current_member
from app.case_types import (
    ALIGNMENT_LABEL, ALIGNMENTS, CASE_TYPES, DEFAULT_CASE_TYPE, PARTY_ROLES, brief_title, case_type_of, default_addressee,
    procedure_for, procedure_warning,
)
from app.db import tenant_session
from app.ingest.extract import UnreadableFile, UnsupportedFile
from app.ingest.service import current_page_text, ingest_document
from app.models import Case, CaseParty, Document, Exhibit, Member, Page, Round

router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _text(n: int):
    """去頭尾空白、長度上限與資料表欄位一致（超長回 422，不是 500）。"""
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]


def _known_type(v: str | None) -> str | None:
    if v is not None and v not in CASE_TYPES:
        raise ValueError(f"不支援的案型：{v}")
    return v


class CaseHeader(BaseModel):
    court: _text(100) | None = None
    case_number: _text(100) | None = None
    division: _text(50) | None = None
    cause: _text(200) | None = None
    addressee: _text(200) | None = None
    attorneys: list[_text(100)] | None = Field(default=None, max_length=10)
    reference_date: date | None = None


class CaseBody(CaseHeader):
    title: _text(300)
    our_side: Literal["plaintiff", "defendant"]
    case_type: str = DEFAULT_CASE_TYPE

    @field_validator("case_type")
    @classmethod
    def check_case_type(cls, v):
        return _known_type(v)


class CasePatch(CaseHeader):
    title: _text(300) | None = None
    our_side: Literal["plaintiff", "defendant"] | None = None
    case_type: str | None = None

    @field_validator("case_type")
    @classmethod
    def check_case_type(cls, v):
        return _known_type(v)


class PartyBody(BaseModel):
    id: UUID | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    role: Literal[PARTY_ROLES]
    alignment: Literal[ALIGNMENTS]
    legal_rep: _text(200) | None = None
    position_note: _text(1000) | None = None


def _case_or_404(s, case_id: UUID) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "找不到案件")
    return case


def _case_summary(c: Case) -> dict:
    return {"id": str(c.id), "title": c.title, "our_side": c.our_side, "case_type": c.case_type}


def _party_json(p: CaseParty) -> dict:
    return {"id": str(p.id), "seq": p.seq, "name": p.name, "role": p.role, "alignment": p.alignment,
            "legal_rep": p.legal_rep, "position_note": p.position_note}


def case_parties(s, case_id) -> list[CaseParty]:
    return list(s.scalars(select(CaseParty).where(CaseParty.case_id == case_id).order_by(CaseParty.seq)))


def _header_json(c: Case) -> dict:
    return {"court": c.court, "case_number": c.case_number, "division": c.division, "cause": c.cause,
            "addressee": c.addressee, "attorneys": list(c.attorneys or []),
            "reference_date": c.reference_date.isoformat() if c.reference_date else None}


@router.post("/cases")
def create_case(body: CaseBody, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        attorneys = body.attorneys
        if attorneys is None:  # 預設帶入建立者；助理不是訴訟代理人
            name = s.scalar(select(Member.display_name).where(Member.id == m.id))
            attorneys = [f"{name}律師"] if m.role == "lawyer" and name else []
        c = Case(firm_id=m.firm_id, title=body.title, our_side=body.our_side, case_type=body.case_type,
                 created_by=m.id, attorneys=attorneys,
                 **body.model_dump(include={"court", "case_number", "division", "cause", "addressee",
                                            "reference_date"}))
        s.add(c)
        s.flush()
        return _case_summary(c)


@router.patch("/cases/{case_id}")
def patch_case(case_id: UUID, body: CasePatch, m: CurrentMember = Depends(current_member)) -> dict:
    with tenant_session(m.firm_id) as s:
        case = _case_or_404(s, case_id)
        for key, value in body.model_dump(exclude_unset=True).items():
            if key in ("title", "our_side", "case_type") and value is None:
                continue  # 必填欄位不能清空
            setattr(case, key, [] if key == "attorneys" and value is None else value)
        s.flush()
        return _case_summary(case)


@router.put("/cases/{case_id}/parties")
def put_parties(case_id: UUID, body: Annotated[list[PartyBody], Field(max_length=50)],
                m: CurrentMember = Depends(current_member)) -> list[dict]:
    """依序整批替換：有帶 id 的保留原列（遞狀人不受影響），沒帶到的刪除（遞狀人隨之變空白）。"""
    with tenant_session(m.firm_id) as s:
        _case_or_404(s, case_id)
        existing = {p.id: p for p in case_parties(s, case_id)}
        unknown = [str(b.id) for b in body if b.id is not None and b.id not in existing]
        if unknown:
            raise HTTPException(422, f"當事人不屬於本案：{', '.join(unknown)}")
        keep = {b.id for b in body if b.id is not None}
        for pid, p in existing.items():
            if pid not in keep:
                s.delete(p)
        s.flush()
        out = []
        for seq, b in enumerate(body, start=1):
            p = existing.get(b.id) if b.id is not None else None
            if p is None:
                p = CaseParty(firm_id=m.firm_id, case_id=case_id)
                s.add(p)
            p.seq, p.name, p.role, p.alignment = seq, b.name, b.role, b.alignment
            p.legal_rep, p.position_note = b.legal_rep, b.position_note
            out.append(p)
        s.flush()
        return [_party_json(p) for p in out]


@router.get("/case-options")
def case_options(m: CurrentMember = Depends(current_member)) -> dict:
    """建立／修改案件表單的選項：案型、當事人角色、歸屬（前端不另存一份）。"""
    return {"case_types": [{"code": t.code, "label": t.label, "procedure": t.procedure,
                            "reference_date_label": t.reference_date_label} for t in CASE_TYPES.values()],
            "party_roles": list(PARTY_ROLES),
            "alignments": [{"code": a, "label": ALIGNMENT_LABEL[a]} for a in ALIGNMENTS]}


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
        procedure = procedure_for(case.case_type, case.case_number)
        return {
            **_case_summary(case),
            "header": _header_json(case),
            "parties": [_party_json(p) for p in case_parties(s, case_id)],
            "procedure": procedure,
            "brief_title": brief_title(procedure),
            "addressee_default": default_addressee(case.court, procedure),
            "procedure_warning": procedure_warning(case.case_type, case.case_number),
            "reference_date_label": case_type_of(case.case_type).reference_date_label,
            "documents": [{"id": str(d.id), "kind": d.kind, "filename": d.filename,
                           "round_id": str(d.round_id) if d.round_id else None,
                           "filed_by_party_id": str(d.filed_by_party_id) if d.filed_by_party_id else None,
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
                          filed_by_party_id: UUID | None = Form(None),
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
        if filed_by_party_id is not None:
            party = s.get(CaseParty, filed_by_party_id)
            if party is None or party.case_id != case_id:
                raise HTTPException(422, "遞狀人不是本案的當事人")
        try:
            doc = ingest_document(s, m.firm_id, case_id, kind, file.filename or "未命名", data, round_id)
            doc.filed_by_party_id = filed_by_party_id
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
