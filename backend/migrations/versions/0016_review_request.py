"""「儲存並標記已審」的審核待完成紀錄（自動化與審核減負，設計 5.2）

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paragraphs", sa.Column("review_request_by", sa.UUID(), nullable=True))
    op.add_column("paragraphs", sa.Column("review_request_version", sa.String(length=64), nullable=True))
    op.add_column("paragraphs", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_foreign_key("paragraphs_firm_id_review_request_by_fkey", "paragraphs", "members",
                          ["firm_id", "review_request_by"], ["firm_id", "id"])


def downgrade() -> None:
    op.drop_constraint("paragraphs_firm_id_review_request_by_fkey", "paragraphs", type_="foreignkey")
    for col in ("review_note", "review_request_version", "review_request_by"):
        op.drop_column("paragraphs", col)
