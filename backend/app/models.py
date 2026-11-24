"""全部 ORM 模型。業務表皆帶 firm_id，由 row-level security 依事務所隔離；
判決與法條為公開表，不帶 firm_id。

外鍵檢查與唯一性檢查會繞過 row-level security，所以業務表之間的外鍵一律是
(firm_id, 父表 id) 的複合外鍵：子列只能指向同一事務所的父列。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String,
    Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.case_types import ALIGNMENTS, CASE_TYPES, PARTY_ROLES
from app.ocr.state import OCR_STATES

ROUND_STATUSES = ("created", "extracting", "extracted", "drafting", "drafted", "failed")
RUNNING_STATUSES = ("extracting", "drafting")
BRIEF_KIND_CODES = ("complaint", "answer", "preparatory")


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


def _tags() -> Mapped[list]:
    """案型標籤（JSONB 字串陣列，可多個；沒有標籤＝空陣列）。"""
    return mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


def _jsonb_list() -> Mapped[list]:
    return mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


def _flag() -> Mapped[bool]:
    """預設 False 的布林欄位（類別裡有 text 欄位時不能直接呼叫 sqlalchemy.text）。"""
    return mapped_column(Boolean, default=False, server_default=text("false"))


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
        _enum("case_type", tuple(CASE_TYPES), "ck_case_type"),
        _tenant_key("cases"),
        _tfk("created_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    title: Mapped[str] = mapped_column(String(300))
    our_side: Mapped[str] = mapped_column(String(20))
    case_type: Mapped[str] = mapped_column(String(50), default="traffic_tort")
    # 狀頭欄位（設計 5.2）：都可以先空著，匯出時留空白讓律師補
    court: Mapped[str | None] = mapped_column(String(100))
    case_number: Mapped[str | None] = mapped_column(String(100))
    division: Mapped[str | None] = mapped_column(String(50))
    cause: Mapped[str | None] = mapped_column(String(200))
    addressee: Mapped[str | None] = mapped_column(String(200))  # 受文，空白時依法院與程序產生預設值
    attorneys: Mapped[list] = _tags()  # 訴訟代理人名單（字串陣列），匯出狀頭用這份，不用匯出者
    reference_date: Mapped[date | None] = mapped_column(Date)  # 基準日，名稱依案型
    created_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    created_at: Mapped[datetime] = _created()


class CaseParty(Base):
    """案件當事人：狀頭與給 AI 的補充資訊。不存地址與身分證字號。"""
    __tablename__ = "case_parties"
    __table_args__ = (
        _enum("role", PARTY_ROLES, "ck_party_role"),
        _enum("alignment", ALIGNMENTS, "ck_party_alignment"),
        _tenant_key("case_parties"),
        _tfk("case_id", "cases", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    alignment: Mapped[str] = mapped_column(String(20))
    legal_rep: Mapped[str | None] = mapped_column(String(200))  # 法定代理人
    position_note: Mapped[str | None] = mapped_column(Text)  # 立場說明，例如「同意分割，但爭執遺產範圍」


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (
        _enum("status", ROUND_STATUSES, "ck_round_status"),
        _enum("brief_kind", BRIEF_KIND_CODES, "ck_round_brief_kind"),
        _enum("failed_stage", ("extract", "draft"), "ck_round_failed_stage"),
        _enum("statement_variant", ("alive", "deceased"), "ck_round_statement_variant"),
        UniqueConstraint("case_id", "seq", name="uq_round_case_seq"),
        _tenant_key("rounds"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("copied_from_round_id", "rounds", "SET NULL (copied_from_round_id)"),
        _tfk("amounts_checked_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)
    # 兩段式：已建立 → 拆解中 → 待確認 → 起草中 → 已起草；任一段失敗為 failed，failed_stage 記是哪一段
    status: Mapped[str] = mapped_column(String(20), default="created")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    brief_kind: Mapped[str] = mapped_column(String(20), default="preparatory", server_default="preparatory")
    failed_stage: Mapped[str | None] = mapped_column(String(10))
    copied_from_round_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text)  # 起訴狀的案情說明
    statement_text: Mapped[str | None] = mapped_column(Text)  # 複製輪次時帶過來的聲明文字
    statement_variant: Mapped[str | None] = mapped_column(String(20))  # 借名登記：出名人在世或已死亡
    joint_liability: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    provisional_execution: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    claim_value: Mapped[int | None] = mapped_column(BigInteger)  # 訴訟標的金額或價額（律師填寫）
    amounts_checked_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    amounts_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        _enum("kind", ("evidence", "opponent_brief"), "ck_document_kind"),
        _tenant_key("documents"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("round_id", "rounds"),
        # 當事人被刪掉時只把遞狀人清成空白，firm_id 保留（PostgreSQL 15 起支援指定欄位的 SET NULL）
        _tfk("filed_by_party_id", "case_parties", "SET NULL (filed_by_party_id)"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    round_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    filed_by_party_id: Mapped[uuid.UUID | None] = _ref(nullable=True)  # 對造書狀的遞狀人
    kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    # 這份卷證最後一次送 AI 產生說明時的原文指紋；與目前指紋不同＝要重新處理（設計 5.6）
    notes_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_images: Mapped[list | None] = mapped_column(JSONB)  # 照片原檔 [{"key","filename"}]（計畫 2A）
    scan_hint: Mapped[bool] = _flag()  # 看起來是掃描後加文字層


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_page_doc_no"),
        _enum("ocr_state", OCR_STATES, "ck_page_ocr_state"),
        _tenant_key("pages"),
        _tfk("document_id", "documents", "CASCADE"),
        _tfk("blank_confirmed_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    document_id: Mapped[uuid.UUID] = _ref()
    page_no: Mapped[int] = mapped_column(Integer)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False)  # 上傳時沒有文字層
    image_key: Mapped[str | None] = mapped_column(String(300))  # 原圖 PNG（第一次需要時產生）
    ocr_state: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    ocr_error: Mapped[str | None] = mapped_column(Text)
    proof: Mapped[list | None] = mapped_column(JSONB)  # 校對員結果 [{"text","score","box"}]
    blank_confirmed_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    blank_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PageText(Base):
    __tablename__ = "page_texts"
    __table_args__ = (
        _enum("source", ("text_layer", "ai_ocr", "manual"), "ck_page_text_source"),
        UniqueConstraint("page_id", "version", name="uq_page_text_version"),
        _tenant_key("page_texts"),
        _tfk("page_id", "pages", "CASCADE"),
        _tfk("verified_by", "members"),
        _tfk("created_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    page_id: Mapped[uuid.UUID] = _ref()
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    verified_against_image: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    # 辨識或手動輸入的文字：寫進書狀前要律師對照原圖（計畫 2A）
    needs_image_check: Mapped[bool] = _flag()
    verified_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    alerts: Mapped[list] = _jsonb_list()


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
    """對造主張（kind=opponent_claim），或起訴狀的我方請求項目（kind=request）。"""
    __tablename__ = "claims"
    __table_args__ = (
        _tfk("round_id", "rounds", "CASCADE"),
        _tfk("page_id", "pages"),
        _tfk("confirmed_by", "members"),
        _enum("kind", ("opponent_claim", "request"), "ck_claim_kind"),
        _enum("request_type", ("monetary", "non_monetary"), "ck_claim_request_type"),
        CheckConstraint("amount >= 0", name="ck_claim_amount"),
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
    selected: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))  # 律師勾選「回應」
    kind: Mapped[str] = mapped_column(String(20), default="opponent_claim", server_default="opponent_claim")
    request_type: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[int | None] = mapped_column(BigInteger)
    amount_note: Mapped[str | None] = mapped_column(String(30))
    basis_text: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list | None] = mapped_column(JSONB)  # [{page_id, quote, found}]
    confirmed_by: Mapped[uuid.UUID | None] = _ref(nullable=True)  # 只有律師能確認請求項目
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Judgment(Base):
    __tablename__ = "judgments"
    __table_args__ = (
        _enum("court_level", ("supreme", "high", "district", "summary"), "ck_judgment_level"),
        _enum("status", ("active", "withdrawn", "corrected"), "ck_judgment_status"),
        UniqueConstraint("source_key", name="uq_judgment_source_key"),
    )
    id: Mapped[uuid.UUID] = _pk()
    citation: Mapped[str] = mapped_column(String(300), unique=True)
    # 司法院網址裡的案件代碼「法院代碼,年,字別,號,日期,序號」：同一件判決只加一次；高院與分院不會混在一起
    source_key: Mapped[str | None] = mapped_column(String(100))
    court: Mapped[str] = mapped_column(String(100))
    court_level: Mapped[str] = mapped_column(String(20))
    decided_on: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    full_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    curated: Mapped[bool] = mapped_column(Boolean, default=False)
    case_types: Mapped[list] = _tags()  # 案型標籤，可多個
    annotation: Mapped[str | None] = mapped_column(Text)  # 例如「少數見解：否定配偶權」，給 AI 與律師看


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
    case_types: Mapped[list] = _tags()
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
    # 建候選時這段是不是律師釘選；之後取消釘選不改變舊輪次的標示
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


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
        _enum("kind", ("body", "statement"), "ck_paragraph_kind"),
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
    kind: Mapped[str] = mapped_column(String(20), default="body", server_default="body")  # statement＝訴之聲明、答辯聲明
    amounts_confirmed: Mapped[list] = _tags()  # 律師按「已核對金額」放行的金額（欄位名 text 遮蔽了 sqlalchemy.text，借用 _tags）
    source_notice: Mapped[str | None] = mapped_column(Text)  # 例：「依據頁已修正」（計畫 2A）


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
        _enum("action", ("edit", "rewrite", "confirm_citation", "confirm_court_reasoning", "mark_reviewed", "revert",
                         "confirm_amount"), "ck_review_action"),
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
        _enum("result", ("ok", "failed"), "ck_usage_result"),
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
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)  # 快取寫入合計
    # 來源分開回報時才有值（5 分鐘與 1 小時的單價不同）；沒分開時是 NULL，以 1 小時單價計
    cache_write_5m_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_1h_tokens: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(10), default="ok", server_default="ok")  # 失敗與內部重試也記
    created_at: Mapped[datetime] = _created()


class AIPrice(Base):
    """AI 單價（每百萬 token，美元），事務所自己的資料；只有事務所管理員能改（設計 5.12）。"""
    __tablename__ = "ai_prices"
    __table_args__ = (
        UniqueConstraint("firm_id", "source", "model", name="uq_ai_price"),
        _enum("billing", ("metered", "unmetered"), "ck_ai_price_billing"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    source: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    output_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cache_read_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cache_write_5m_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cache_write_1h_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    billing: Mapped[str] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(Text, default="")


class CostSetting(Base):
    """美元兌新台幣匯率（每家事務所一列）；律師與事務所管理員可以改。"""
    __tablename__ = "cost_settings"
    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id"), primary_key=True)
    usd_twd: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    rate_set_on: Mapped[date] = mapped_column(Date)
    is_default: Mapped[bool] = mapped_column(Boolean)


class ExportRecord(Base):
    __tablename__ = "export_records"
    __table_args__ = (
        _tfk("draft_id", "drafts"),
        _tfk("member_id", "members"),
        _enum("kind", ("docx", "package"), "ck_export_kind"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    draft_id: Mapped[uuid.UUID] = _ref()
    member_id: Mapped[uuid.UUID] = _ref()
    kind: Mapped[str] = mapped_column(String(10), default="docx", server_default="docx")  # Word 或遞狀包
    bar_number: Mapped[str | None] = mapped_column(String(50))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


AI_JOB_STEPS = ("evidence_notes", "timeline", "health", "ocr")
AI_JOB_STATUSES = ("running", "done", "failed", "waiting")  # waiting＝辨識頁數超過上限，等律師確認


class AIJob(Base):
    """按鈕觸發的 AI 工作（卷證說明、大事記、健檢）：前端輪詢狀態；心跳逾時可重新開始。"""
    __tablename__ = "ai_jobs"
    __table_args__ = (
        _enum("step", AI_JOB_STEPS, "ck_ai_job_step"),
        _enum("status", AI_JOB_STATUSES, "ck_ai_job_status"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("round_id", "rounds", "CASCADE"),
        _tfk("member_id", "members"),
        _tfk("document_id", "documents", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    round_id: Mapped[uuid.UUID | None] = _ref(nullable=True)  # 健檢才有輪次
    document_id: Mapped[uuid.UUID | None] = _ref(nullable=True)  # 辨識工作掛在文件上
    step: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), default="running")
    member_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    started_at: Mapped[datetime] = _created()
    heartbeat_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class PinnedPassage(Base):
    """律師在案件裡釘選的判決法院判斷段（設計 5.9）：之後每一輪建候選時排在最前面。"""
    __tablename__ = "pinned_passages"
    __table_args__ = (
        UniqueConstraint("case_id", "judgment_passage_id", name="uq_pinned_passage"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("pinned_by", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    judgment_passage_id: Mapped[uuid.UUID] = _public_fk("judgment_passages.id", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    pinned_by: Mapped[uuid.UUID | None] = _ref(nullable=True)
    created_at: Mapped[datetime] = _created()


class JudgmentImport(Base):
    """律師貼司法院網址加入判決的紀錄：哪個事務所、哪位使用者、哪個案件、加了哪一件。"""
    __tablename__ = "judgment_imports"
    __table_args__ = (
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("member_id", "members"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    member_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    case_id: Mapped[uuid.UUID] = _ref()
    judgment_id: Mapped[uuid.UUID] = _public_fk("judgments.id", nullable=False)
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class EvidenceNote(Base):
    """卷證說明（設計 5.6）：每份我方卷證的證明方向與注意事項，每句帶原文出處。"""
    __tablename__ = "evidence_notes"
    __table_args__ = (
        _enum("kind", ("proves", "caution"), "ck_evidence_note_kind"),
        _enum("status", ("found", "not_found", "absence"), "ck_evidence_note_status"),
        _enum("origin", ("ai", "lawyer"), "ck_evidence_note_origin"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("document_id", "documents", "CASCADE"),
        _tfk("page_id", "pages", "SET NULL (page_id)"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    document_id: Mapped[uuid.UUID] = _ref()
    kind: Mapped[str] = mapped_column(String(10))
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10))
    reasons: Mapped[list] = _jsonb_list()
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    created_at: Mapped[datetime] = _created()


class TimelineEvent(Base):
    """大事記（設計 5.7）：日期、事件、金額、出處；對造書狀來的標「對造主張」。"""
    __tablename__ = "timeline_events"
    __table_args__ = (
        _enum("date_precision", ("day", "month"), "ck_timeline_date_precision"),
        _enum("source_kind", ("evidence", "opponent"), "ck_timeline_source_kind"),
        _enum("origin", ("ai", "lawyer", "dismissed"), "ck_timeline_origin"),  # dismissed：律師刪掉的 AI 事件
        _enum("status", ("found", "not_found", "unchecked"), "ck_timeline_status"),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("page_id", "pages", "SET NULL (page_id)"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    case_id: Mapped[uuid.UUID] = _ref()
    seq: Mapped[int] = mapped_column(Integer)  # 同一天的事件依這個排（同一筆交易寫入的建立時間相同）
    event_date: Mapped[date | None] = mapped_column(Date)
    date_precision: Mapped[str | None] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    amount: Mapped[int | None] = mapped_column(BigInteger)
    page_id: Mapped[uuid.UUID | None] = _ref(nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str | None] = mapped_column(String(10))
    origin: Mapped[str] = mapped_column(String(10), default="ai")
    status: Mapped[str] = mapped_column(String(10))
    reasons: Mapped[list] = _jsonb_list()
    created_at: Mapped[datetime] = _created()


class HealthReport(Base):
    """書狀健檢與爭點整理表（設計 5.8）：每輪最新一份。出處只存頁面 id 與摘錄，標籤讀取時才算。"""
    __tablename__ = "health_reports"
    __table_args__ = (
        UniqueConstraint("round_id", name="uq_health_report_round"),
        _tfk("round_id", "rounds", "CASCADE"),
    )
    id: Mapped[uuid.UUID] = _pk()
    firm_id: Mapped[uuid.UUID] = _firm()
    round_id: Mapped[uuid.UUID] = _ref()
    created_at: Mapped[datetime] = _created()
    fingerprint: Mapped[str] = mapped_column(String(64))
    items: Mapped[list] = _jsonb_list()
    undisputed: Mapped[list | None] = mapped_column(JSONB)  # 起訴狀沒有（起訴時尚無對造陳述）
    issues: Mapped[list] = _jsonb_list()


class WorkCalendarDay(Base):
    """行政機關辦公日曆（全系統共用、唯讀）：期限屆滿日遇放假要順延（民法第122條）。"""
    __tablename__ = "work_calendar"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    is_holiday: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(Text, default="")


PUBLIC_TABLES = ("judgments", "judgment_passages", "statutes", "statute_articles", "work_calendar")
# 合規門檻欄位（is_internal_test、bar_verified、role）所在的表：一般請求用的帳號只能讀。
READ_ONLY_FOR_APP = ("firms", "members")


def business_tables() -> list[str]:
    return [t.name for t in Base.metadata.sorted_tables if "firm_id" in t.c or t.name == "firms"]
