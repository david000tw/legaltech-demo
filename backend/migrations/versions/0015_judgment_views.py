"""律師在草稿裡打開過哪些判決段落的原文（自動化與審核減負，設計 5.1）

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"


def _tfk(col: str, table: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["firm_id", col], [f"{table}.firm_id", f"{table}.id"], ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "judgment_views",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("draft_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("judgment_passage_id", sa.UUID(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.ForeignKeyConstraint(["judgment_passage_id"], ["judgment_passages.id"], ondelete="CASCADE"),
        _tfk("draft_id", "drafts", "CASCADE"),
        _tfk("member_id", "members", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "member_id", "judgment_passage_id", name="uq_judgment_view"),
    )
    op.execute("ALTER TABLE judgment_views ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE judgment_views FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON judgment_views "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON judgment_views TO app_user")


def downgrade() -> None:
    op.drop_table("judgment_views")
