"""對方書狀附的證物（對方證物計畫）：規則找出的編號與起訖頁

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-26
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "opponent_exhibits",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("prefix", sa.String(length=10), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("first_page_no", sa.Integer(), nullable=False),
        sa.Column("last_page_no", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.ForeignKeyConstraint(["firm_id", "document_id"], ["documents.firm_id", "documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "first_page_no", name="uq_opponent_exhibit_start"),
    )
    op.execute("ALTER TABLE opponent_exhibits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE opponent_exhibits FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON opponent_exhibits "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON opponent_exhibits TO app_user")


def downgrade() -> None:
    op.drop_table("opponent_exhibits")
