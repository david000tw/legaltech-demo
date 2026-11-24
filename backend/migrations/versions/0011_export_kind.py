"""匯出紀錄分 Word（docx）與電子遞狀包（package）；舊紀錄都是 docx

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("export_records", sa.Column("kind", sa.String(length=10), server_default="docx", nullable=False))
    op.create_check_constraint("ck_export_kind", "export_records", "kind in ('docx', 'package')")


def downgrade() -> None:
    op.drop_constraint("ck_export_kind", "export_records", type_="check")
    op.drop_column("export_records", "kind")
