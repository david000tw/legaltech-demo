"""案型：判決與法條的案型標籤、判決註記（少數見解等）、案件案型限定四種

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

CASE_TYPES = ("traffic_tort", "spousal_rights", "estate_partition", "nominee_registration")  # 凍結當時的登錄表


def upgrade() -> None:
    op.add_column("judgments", sa.Column("case_types", postgresql.JSONB(astext_type=sa.Text()),
                                         server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("judgments", sa.Column("annotation", sa.Text(), nullable=True))
    op.add_column("statute_articles", sa.Column("case_types", postgresql.JSONB(astext_type=sa.Text()),
                                                server_default=sa.text("'[]'::jsonb"), nullable=False))
    quoted = ", ".join(f"'{c}'" for c in CASE_TYPES)
    op.create_check_constraint("ck_case_type", "cases", f"case_type in ({quoted})")


def downgrade() -> None:
    op.drop_constraint("ck_case_type", "cases", type_="check")
    op.drop_column("statute_articles", "case_types")
    op.drop_column("judgments", "annotation")
    op.drop_column("judgments", "case_types")
