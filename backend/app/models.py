"""全部 ORM 模型。業務表皆帶 firm_id，由 row-level security 依事務所隔離；
判決與法條為公開表，不帶 firm_id。

外鍵檢查與唯一性檢查會繞過 row-level security，所以業務表之間的外鍵一律是
(firm_id, 父表 id) 的複合外鍵：子列只能指向同一事務所的父列。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _firm() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("firms.id"), nullable=False)


def _ref(*, nullable: bool = False) -> Mapped:
    """指向另一張業務表的欄位；外鍵由 _tfk 以複合鍵宣告。"""
    return mapped_column(UUID(as_uuid=True), nullable=nullable)


def _public_fk(target: str, *, nullable: bool = True, ondelete: str | None = None) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _tfk(col: str, table: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["firm_id", col], [f"{table}.firm_id", f"{table}.id"], ondelete=ondelete)


def _tenant_key(table: str) -> UniqueConstraint:
    """讓子表可以用 (firm_id, id) 複合外鍵指向本表。"""
    return UniqueConstraint("firm_id", "id", name=f"uq_{table}_firm_id")


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _enum(col: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} in ({quoted})", name=name)


class Firm(Base):
    __tablename__ = "firms"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    is_internal_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        _enum("role", ("lawyer", "assistant", "firm_admin"), "ck_member_role"),
        _tenant_key("members"),
        Index("uq_members_email_lower", text("lower(email)"), unique=True),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    bar_number: Mapped[str | None] = mapped_column(String(50))
    bar_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        _enum("our_side", ("plaintiff", "defendant"), "ck_case_side"),
        _tenant_key("cases"),
        _tfk("created_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    title: Mapped[str] = mapped_column(String(300))
    our_side: Mapped[str] = mapped_column(String(20))
    case_type: Mapped[str] = mapped_column(String(50), default="traffic_tort")
    created_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    created_at: Mapped[datetime] = _created()


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (
        _enum("status", ("created", "running", "drafted", "failed"), "ck_round_status"),
        UniqueConstraint("case_id", "seq", name="uq_round_case_seq"),
        _tenant_key("rounds"),
        _tfk("case_id", "cases", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="created")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        _enum("kind", ("evidence", "opponent_brief"), "ck_document_kind"),
        _tenant_key("documents"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("round_id", "rounds"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    round_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_page_doc_no"),
        _tenant_key("pages"),
        _tfk("document_id", "documents", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    document_id: Mapped[uuid.UUID] = _ref()
    page_no: Mapped[int] = mapped_column(Integer)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False)


class PageText(Base):
    __tablename__ = "page_texts"
    __table_args__ = (
        _enum("source", ("text_layer", "ai_ocr", "manual"), "ck_page_text_source"),
        UniqueConstraint("page_id", "version", name="uq_page_text_version"),
        _tenant_key("page_texts"),
        _tfk("page_id", "pages", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    page_id: Mapped[uuid.UUID] = _ref()
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    verified_against_image: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Exhibit(Base):
    __tablename__ = "exhibits"
    __table_args__ = (
        _enum("prefix", ("原證", "被證"), "ck_exhibit_prefix"),
        UniqueConstraint("case_id", "prefix", "number", name="uq_exhibit_number"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("document_id", "documents", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    document_id: Mapped[uuid.UUID] = _ref()
    prefix: Mapped[str] = mapped_column(String(10))
    number: Mapped[int] = mapped_column(Integer)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        _tfk("round_id", "rounds", "CASCADE"),
        _tfk("page_id", "pages"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    round_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    page_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    selection: Mapped[dict | None] = mapped_column(JSONB)


class Judgment(Base):
    __tablename__ = "judgments"
    __table_args__ = (
        _enum("court_level", ("supreme", "high", "district", "summary"), "ck_judgment_level"),
        _enum("status", ("active", "withdrawn", "corrected"), "ck_judgment_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    citation: Mapped[str] = mapped_column(String(300), unique=True)
    court: Mapped[str] = mapped_column(String(100))
    court_level: Mapped[str] = mapped_column(String(20))
    decided_on: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    full_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    curated: Mapped[bool] = mapped_column(Boolean, default=False)


class JudgmentPassage(Base):
    __tablename__ = "judgment_passages"
    __table_args__ = (
        _enum("passage_type", ("main_text", "party_claim", "court_reasoning", "other"), "ck_passage_type"),
        _enum("status", ("active", "withdrawn", "corrected"), "ck_passage_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    judgment_id: Mapped[uuid.UUID] = _public_fk("judgments.id", nullable=False, ondelete="CASCADE")
    seq: Mapped[int] = mapped_column(Integer)
    passage_type: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    judgment: Mapped[Judgment] = relationship()


class Statute(Base):
    __tablename__ = "statutes"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), unique=True)
    pcode: Mapped[str | None] = mapped_column(String(20))


class StatuteArticle(Base):
    __tablename__ = "statute_articles"
    __table_args__ = (UniqueConstraint("statute_id", "article_no", name="uq_statute_article"),)
    id: Mapped[uuid.UUID] = _pk()
    statute_id: Mapped[uuid.UUID] = _public_fk("statutes.id", nullable=False, ondelete="CASCADE")
    article_no: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date | None] = mapped_column(Date)
    amended_on: Mapped[date | None] = mapped_column(Date)
    statute: Mapped[Statute] = relationship()


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        _enum("kind", ("evidence", "judgment", "statute"), "ck_candidate_kind"),
        UniqueConstraint("round_id", "code", name="uq_candidate_code"),
        _tfk("round_id", "rounds", "CASCADE"),
        _tfk("page_text_id", "page_texts"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    round_id: Mapped[uuid.UUID] = _ref()
    code: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20))
    page_text_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    judgment_passage_id: Mapped[uuid.UUID | None] = _public_fk("judgment_passages.id")
    statute_article_id: Mapped[uuid.UUID | None] = _public_fk("statute_articles.id")


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        _tenant_key("drafts"),
        _tfk("round_id", "rounds", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    round_id: Mapped[uuid.UUID] = _ref()
    created_at: Mapped[datetime] = _created()


class Paragraph(Base):
    __tablename__ = "paragraphs"
    __table_args__ = (
        _enum("status", ("unreviewed", "reviewed"), "ck_paragraph_status"),
        _tenant_key("paragraphs"),
        _tfk("draft_id", "drafts", "CASCADE"),
        _tfk("reviewed_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    draft_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="unreviewed")
    verify_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    reviewed_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    reviewed_bar_number: Mapped[str | None] = mapped_column(String(50))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        _enum("kind", ("evidence", "judgment", "statute"), "ck_citation_kind"),
        _enum("verify_status", ("ok", "warn", "failed"), "ck_citation_status"),
        _tfk("paragraph_id", "paragraphs", "CASCADE"),
        _tfk("page_text_id", "page_texts"),
        _tfk("confirmed_by", "members"),
        _tfk("court_confirmed_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    paragraph_id: Mapped[uuid.UUID] = _ref()
    code: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20))
    quote: Mapped[str] = mapped_column(Text, default="")
    page_text_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    judgment_passage_id: Mapped[uuid.UUID | None] = _public_fk("judgment_passages.id")
    statute_article_id: Mapped[uuid.UUID | None] = _public_fk("statute_articles.id")
    verify_status: Mapped[str] = mapped_column(String(10), default="failed")
    verify_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    lawyer_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    # warn（可能是法院轉述的當事人主張）的引用，律師讀原文後確認「此句為法院判斷」才放行（設計 5.5）
    court_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    court_confirmed_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    court_confirmed_bar_number: Mapped[str | None] = mapped_column(String(50))


class ReviewEvent(Base):
    __tablename__ = "review_events"
    __table_args__ = (
        _enum("action", ("edit", "rewrite", "confirm_citation", "confirm_court_reasoning", "mark_reviewed", "revert"),
              "ck_review_action"),
        _tfk("paragraph_id", "paragraphs", "CASCADE"),
        _tfk("member_id", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    paragraph_id: Mapped[uuid.UUID] = _ref()
    member_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    before_text: Mapped[str | None] = mapped_column(Text)
    after_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        _tfk("case_id", "cases"),
        _tfk("member_id", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    member_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    step: Mapped[str] = mapped_column(String(50))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class ExportRecord(Base):
    __tablename__ = "export_records"
    __table_args__ = (
        _tfk("draft_id", "drafts"),
        _tfk("member_id", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    draft_id: Mapped[uuid.UUID] = _ref()
    member_id: Mapped[uuid.UUID] = _ref()
    bar_number: Mapped[str | None] = mapped_column(String(50))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


PUBLIC_TABLES = ("judgments", "judgment_passages", "statutes", "statute_articles")
# 合規門檻欄位（is_internal_test、bar_verified、role）所在的表：一般請求用的帳號只能讀。
READ_ONLY_FOR_APP = ("firms", "members")


def business_tables() -> list[str]:
    return [t.name for t in Base.metadata.sorted_tables if "firm_id" in t.c or t.name == "firms"]
