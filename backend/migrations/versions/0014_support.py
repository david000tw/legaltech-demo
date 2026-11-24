"""計畫 2B：支持度檢查結果表、審核紀錄多一種「放行支持度」

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
# 凍結當時的清單
OLD_ACTIONS = ("edit", "rewrite", "confirm_citation", "confirm_court_reasoning", "mark_reviewed", "revert",
               "confirm_amount")
KINDS = ("evidence", "judgment", "statute")
STATUSES = ("pending", "done", "failed")
VERDICTS = ("supports", "partial", "not_supported", "party_claim_misuse")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _tfk(col: str, table: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["firm_id", col], [f"{table}.firm_id", f"{table}.id"], ondelete=ondelete)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "support_checks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="pending", nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        _ts("claimed_at"),
        _ts("checked_at"),
        sa.Column("released_by", sa.UUID(), nullable=True),
        sa.Column("released_bar_number", sa.String(length=50), nullable=True),
        _ts("released_at"),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"kind in ({_in(KINDS)})", name="ck_support_kind"),
        sa.CheckConstraint(f"status in ({_in(STATUSES)})", name="ck_support_status"),
        sa.CheckConstraint(f"verdict in ({_in(VERDICTS)})", name="ck_support_verdict"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        _tfk("case_id", "cases", "CASCADE"),
        _tfk("released_by", "members"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "fingerprint", name="uq_support_check_fp"),
    )
    op.execute("ALTER TABLE support_checks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_checks FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON support_checks "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON support_checks TO app_user")

    op.drop_constraint("ck_review_action", "review_events", type_="check")
    op.create_check_constraint("ck_review_action", "review_events",
                               f"action in ({_in(OLD_ACTIONS + ('release_support',))})")


def downgrade() -> None:
    op.execute("DELETE FROM review_events WHERE action = 'release_support'")
    op.drop_constraint("ck_review_action", "review_events", type_="check")
    op.create_check_constraint("ck_review_action", "review_events", f"action in ({_in(OLD_ACTIONS)})")
    op.drop_table("support_checks")
