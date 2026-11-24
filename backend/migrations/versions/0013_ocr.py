"""計畫 2A：頁的辨識狀態與原圖、頁文字的對照欄位、段落加註、AI 工作掛在文件上

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

OCR_STATES = ("none", "pending", "read", "done", "failed")
OLD_STEPS = ("evidence_notes", "timeline", "health")
OLD_STATUSES = ("running", "done", "failed")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _tfk(table: str, col: str, target: str, ondelete: str | None = None) -> None:
    op.create_foreign_key(f"{table}_firm_id_{col}_fkey", table, target, ["firm_id", col], ["firm_id", "id"],
                          ondelete=ondelete)


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_images", JSONB(), nullable=True))
    op.add_column("documents", sa.Column("scan_hint", sa.Boolean(), server_default=sa.text("false"), nullable=False))

    op.add_column("pages", sa.Column("image_key", sa.String(length=300), nullable=True))
    op.add_column("pages", sa.Column("ocr_state", sa.String(length=10), server_default="none", nullable=False))
    op.add_column("pages", sa.Column("ocr_error", sa.Text(), nullable=True))
    op.add_column("pages", sa.Column("proof", JSONB(), nullable=True))
    op.add_column("pages", sa.Column("blank_confirmed_by", sa.UUID(), nullable=True))
    op.add_column("pages", sa.Column("blank_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_page_ocr_state", "pages", f"ocr_state in ({_in(OCR_STATES)})")
    _tfk("pages", "blank_confirmed_by", "members")
    # 沒有文字層、也還沒有任何頁文字的頁＝待辨識；其餘是檔案本身有文字
    op.execute("UPDATE pages SET ocr_state = 'pending' WHERE needs_ocr "
               "AND NOT EXISTS (SELECT 1 FROM page_texts pt WHERE pt.page_id = pages.id)")

    op.add_column("page_texts", sa.Column("needs_image_check", sa.Boolean(), server_default=sa.text("false"),
                                          nullable=False))
    op.add_column("page_texts", sa.Column("verified_by", sa.UUID(), nullable=True))
    op.add_column("page_texts", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("page_texts", sa.Column("created_by", sa.UUID(), nullable=True))
    op.add_column("page_texts", sa.Column("alerts", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    _tfk("page_texts", "verified_by", "members")
    _tfk("page_texts", "created_by", "members")

    op.add_column("paragraphs", sa.Column("source_notice", sa.Text(), nullable=True))

    op.add_column("ai_jobs", sa.Column("document_id", sa.UUID(), nullable=True))
    _tfk("ai_jobs", "document_id", "documents", "CASCADE")
    op.drop_constraint("ck_ai_job_step", "ai_jobs", type_="check")
    op.create_check_constraint("ck_ai_job_step", "ai_jobs", f"step in ({_in(OLD_STEPS + ('ocr',))})")
    op.drop_constraint("ck_ai_job_status", "ai_jobs", type_="check")
    op.create_check_constraint("ck_ai_job_status", "ai_jobs", f"status in ({_in(OLD_STATUSES + ('waiting',))})")


def downgrade() -> None:
    op.execute("DELETE FROM ai_jobs WHERE step = 'ocr' OR status = 'waiting'")
    op.drop_constraint("ck_ai_job_status", "ai_jobs", type_="check")
    op.create_check_constraint("ck_ai_job_status", "ai_jobs", f"status in ({_in(OLD_STATUSES)})")
    op.drop_constraint("ck_ai_job_step", "ai_jobs", type_="check")
    op.create_check_constraint("ck_ai_job_step", "ai_jobs", f"step in ({_in(OLD_STEPS)})")
    op.drop_constraint("ai_jobs_firm_id_document_id_fkey", "ai_jobs", type_="foreignkey")
    op.drop_column("ai_jobs", "document_id")

    op.drop_column("paragraphs", "source_notice")

    op.drop_constraint("page_texts_firm_id_created_by_fkey", "page_texts", type_="foreignkey")
    op.drop_constraint("page_texts_firm_id_verified_by_fkey", "page_texts", type_="foreignkey")
    for col in ("alerts", "created_by", "verified_at", "verified_by", "needs_image_check"):
        op.drop_column("page_texts", col)

    op.drop_constraint("pages_firm_id_blank_confirmed_by_fkey", "pages", type_="foreignkey")
    op.drop_constraint("ck_page_ocr_state", "pages", type_="check")
    for col in ("blank_confirmed_at", "blank_confirmed_by", "proof", "ocr_error", "ocr_state", "image_key"):
        op.drop_column("pages", col)

    op.drop_column("documents", "scan_hint")
    op.drop_column("documents", "source_images")
