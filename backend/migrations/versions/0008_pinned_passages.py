"""判決釘選：pinned_passages（RLS），候選記下是不是律師釘選

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column("candidates", sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_table(
        "pinned_passages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("judgment_passage_id", sa.UUID(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("pinned_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.ForeignKeyConstraint(["firm_id", "case_id"], ["cases.firm_id", "cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["firm_id", "pinned_by"], ["members.firm_id", "members.id"]),
        sa.ForeignKeyConstraint(["judgment_passage_id"], ["judgment_passages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "judgment_passage_id", name="uq_pinned_passage"),
    )
    op.execute("ALTER TABLE pinned_passages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pinned_passages FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON pinned_passages "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pinned_passages TO app_user")


def downgrade() -> None:
    op.drop_table("pinned_passages")
    op.drop_column("candidates", "pinned")
