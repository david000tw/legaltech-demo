"""段落回應的主張編號（自動化與審核減負，設計 6.5）；NULL＝功能上線前產生的舊草稿

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paragraphs", sa.Column("claim_seqs", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("paragraphs", "claim_seqs")
