"""從對方書狀自動帶入的狀頭欄位與對造當事人（自動化與審核減負，設計 6.6）

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("header_auto", postgresql.JSONB(astext_type=sa.Text()),
                                     server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("case_parties", sa.Column("auto_document_id", sa.UUID(), nullable=True))
    op.add_column("case_parties", sa.Column("auto_verified", sa.Boolean(), server_default=sa.text("false"),
                                            nullable=False))
    op.create_foreign_key("case_parties_firm_id_auto_document_id_fkey", "case_parties", "documents",
                          ["firm_id", "auto_document_id"], ["firm_id", "id"], ondelete="SET NULL (auto_document_id)")


def downgrade() -> None:
    op.drop_constraint("case_parties_firm_id_auto_document_id_fkey", "case_parties", type_="foreignkey")
    op.drop_column("case_parties", "auto_verified")
    op.drop_column("case_parties", "auto_document_id")
    op.drop_column("cases", "header_auto")
