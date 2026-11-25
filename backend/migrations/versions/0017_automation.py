"""自動化程度、輪次的自動流程欄位、答辯策略（自動化與審核減負，設計 6.1、6.2、6.4）

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("automation", sa.String(length=10), server_default="auto", nullable=False))
    op.add_column("cases", sa.Column("notes_auto_fingerprint", sa.String(length=64), nullable=True))
    op.create_check_constraint("ck_case_automation", "cases", "automation in ('auto', 'pause', 'manual')")
    op.add_column("rounds", sa.Column("auto_next", sa.String(length=10), nullable=True))
    op.add_column("rounds", sa.Column("auto_by", sa.UUID(), nullable=True))
    op.add_column("rounds", sa.Column("auto_retries", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("rounds", sa.Column("auto_note", sa.Text(), nullable=True))
    op.add_column("rounds", sa.Column("extract_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("rounds", sa.Column("strategy", sa.Text(), nullable=True))
    op.create_check_constraint("ck_round_auto_next", "rounds", "auto_next in ('extract', 'draft')")
    op.create_foreign_key("rounds_firm_id_auto_by_fkey", "rounds", "members", ["firm_id", "auto_by"], ["firm_id", "id"])


def downgrade() -> None:
    op.drop_constraint("rounds_firm_id_auto_by_fkey", "rounds", type_="foreignkey")
    op.drop_constraint("ck_round_auto_next", "rounds", type_="check")
    for col in ("strategy", "extract_fingerprint", "auto_note", "auto_retries", "auto_by", "auto_next"):
        op.drop_column("rounds", col)
    op.drop_constraint("ck_case_automation", "cases", type_="check")
    op.drop_column("cases", "notes_auto_fingerprint")
    op.drop_column("cases", "automation")
