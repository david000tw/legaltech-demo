"""律師確認「此句為法院判斷」：引用加 warn 狀態與確認欄位，審核事件加 confirm_court_reasoning

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("court_confirmed", sa.Boolean(), server_default=sa.text("false"),
                                         nullable=False))
    op.add_column("citations", sa.Column("court_confirmed_by", sa.UUID(), nullable=True))
    op.add_column("citations", sa.Column("court_confirmed_bar_number", sa.String(length=50), nullable=True))
    op.create_foreign_key("citations_firm_id_court_confirmed_by_fkey", "citations", "members",
                          ["firm_id", "court_confirmed_by"], ["firm_id", "id"])
    op.drop_constraint("ck_citation_status", "citations", type_="check")
    op.create_check_constraint("ck_citation_status", "citations", "verify_status in ('ok', 'warn', 'failed')")
    op.drop_constraint("ck_review_action", "review_events", type_="check")
    op.create_check_constraint(
        "ck_review_action", "review_events",
        "action in ('edit', 'rewrite', 'confirm_citation', 'confirm_court_reasoning', 'mark_reviewed', 'revert')")


def downgrade() -> None:
    op.drop_constraint("ck_review_action", "review_events", type_="check")
    op.create_check_constraint("ck_review_action", "review_events",
                               "action in ('edit', 'rewrite', 'confirm_citation', 'mark_reviewed', 'revert')")
    op.drop_constraint("ck_citation_status", "citations", type_="check")
    op.create_check_constraint("ck_citation_status", "citations", "verify_status in ('ok', 'failed')")
    op.drop_constraint("citations_firm_id_court_confirmed_by_fkey", "citations", type_="foreignkey")
    op.drop_column("citations", "court_confirmed_bar_number")
    op.drop_column("citations", "court_confirmed_by")
    op.drop_column("citations", "court_confirmed")
