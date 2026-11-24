"""三種書狀與兩段式輪次：書狀種類、輪次新狀態、主張勾選與請求項目欄位、聲明段落、金額核對紀錄

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# 凍結當時的清單
BRIEF_KINDS = ("complaint", "answer", "preparatory")
ROUND_STATUSES = ("created", "extracting", "extracted", "drafting", "drafted", "failed")
OLD_ROUND_STATUSES = ("created", "running", "drafted", "failed")
STAGES = ("extract", "draft")
VARIANTS = ("alive", "deceased")
CLAIM_KINDS = ("opponent_claim", "request")
REQUEST_TYPES = ("monetary", "non_monetary")
PARAGRAPH_KINDS = ("body", "statement")
ACTIONS = ("edit", "rewrite", "confirm_citation", "confirm_court_reasoning", "mark_reviewed", "revert", "confirm_amount")
OLD_ACTIONS = ("edit", "rewrite", "confirm_citation", "confirm_court_reasoning", "mark_reviewed", "revert")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _check(name: str, table: str, col: str, values) -> None:
    op.create_check_constraint(name, table, f"{col} in ({_in(values)})")


def upgrade() -> None:
    # 輪次
    op.add_column("rounds", sa.Column("brief_kind", sa.String(length=20), server_default="preparatory", nullable=False))
    op.add_column("rounds", sa.Column("failed_stage", sa.String(length=10), nullable=True))
    op.add_column("rounds", sa.Column("copied_from_round_id", sa.UUID(), nullable=True))
    op.add_column("rounds", sa.Column("narrative", sa.Text(), nullable=True))
    op.add_column("rounds", sa.Column("statement_text", sa.Text(), nullable=True))
    op.add_column("rounds", sa.Column("statement_variant", sa.String(length=20), nullable=True))
    op.add_column("rounds", sa.Column("joint_liability", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("rounds", sa.Column("provisional_execution", sa.Boolean(), server_default=sa.text("true"),
                                      nullable=False))
    op.add_column("rounds", sa.Column("claim_value", sa.BigInteger(), nullable=True))
    op.add_column("rounds", sa.Column("amounts_checked_by", sa.UUID(), nullable=True))
    op.add_column("rounds", sa.Column("amounts_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("rounds_firm_id_copied_from_round_id_fkey", "rounds", "rounds",
                          ["firm_id", "copied_from_round_id"], ["firm_id", "id"],
                          ondelete="SET NULL (copied_from_round_id)")
    op.create_foreign_key("rounds_firm_id_amounts_checked_by_fkey", "rounds", "members",
                          ["firm_id", "amounts_checked_by"], ["firm_id", "id"])
    _check("ck_round_brief_kind", "rounds", "brief_kind", BRIEF_KINDS)
    _check("ck_round_failed_stage", "rounds", "failed_stage", STAGES)
    _check("ck_round_statement_variant", "rounds", "statement_variant", VARIANTS)
    # 舊的「執行中」一律視為中斷；舊的失敗輪次從拆解重來
    op.drop_constraint("ck_round_status", "rounds", type_="check")
    op.execute("UPDATE rounds SET status = 'failed', failed_stage = 'extract', error = '系統升級時中斷，請重新執行' "
               "WHERE status = 'running'")
    op.execute("UPDATE rounds SET failed_stage = 'extract' WHERE status = 'failed' AND failed_stage IS NULL")
    _check("ck_round_status", "rounds", "status", ROUND_STATUSES)

    # 主張（對造主張與我方請求項目）
    op.add_column("claims", sa.Column("selected", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("claims", sa.Column("kind", sa.String(length=20), server_default="opponent_claim", nullable=False))
    op.add_column("claims", sa.Column("request_type", sa.String(length=20), nullable=True))
    op.add_column("claims", sa.Column("amount", sa.BigInteger(), nullable=True))
    op.add_column("claims", sa.Column("amount_note", sa.String(length=30), nullable=True))
    op.add_column("claims", sa.Column("basis_text", sa.Text(), nullable=True))
    op.add_column("claims", sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("claims", sa.Column("confirmed_by", sa.UUID(), nullable=True))
    op.add_column("claims", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("claims_firm_id_confirmed_by_fkey", "claims", "members",
                          ["firm_id", "confirmed_by"], ["firm_id", "id"])
    _check("ck_claim_kind", "claims", "kind", CLAIM_KINDS)
    _check("ck_claim_request_type", "claims", "request_type", REQUEST_TYPES)
    op.create_check_constraint("ck_claim_amount", "claims", "amount >= 0")

    # 段落
    op.add_column("paragraphs", sa.Column("kind", sa.String(length=20), server_default="body", nullable=False))
    op.add_column("paragraphs", sa.Column("amounts_confirmed", postgresql.JSONB(astext_type=sa.Text()),
                                          server_default=sa.text("'[]'::jsonb"), nullable=False))
    _check("ck_paragraph_kind", "paragraphs", "kind", PARAGRAPH_KINDS)

    op.drop_constraint("ck_review_action", "review_events", type_="check")
    _check("ck_review_action", "review_events", "action", ACTIONS)


def downgrade() -> None:
    # 舊結構表達不了金額核對紀錄：先刪掉
    op.execute("DELETE FROM review_events WHERE action = 'confirm_amount'")
    op.drop_constraint("ck_review_action", "review_events", type_="check")
    _check("ck_review_action", "review_events", "action", OLD_ACTIONS)

    op.drop_constraint("ck_paragraph_kind", "paragraphs", type_="check")
    op.drop_column("paragraphs", "amounts_confirmed")
    op.drop_column("paragraphs", "kind")

    for name in ("ck_claim_amount", "ck_claim_request_type", "ck_claim_kind"):
        op.drop_constraint(name, "claims", type_="check")
    op.drop_constraint("claims_firm_id_confirmed_by_fkey", "claims", type_="foreignkey")
    for col in ("confirmed_at", "confirmed_by", "evidence", "basis_text", "amount_note", "amount", "request_type",
                "kind", "selected"):
        op.drop_column("claims", col)

    op.drop_constraint("ck_round_status", "rounds", type_="check")
    op.execute("UPDATE rounds SET status = 'created' WHERE status = 'extracted'")
    op.execute("UPDATE rounds SET status = 'failed' WHERE status IN ('extracting', 'drafting')")
    _check("ck_round_status", "rounds", "status", OLD_ROUND_STATUSES)
    for name in ("ck_round_statement_variant", "ck_round_failed_stage", "ck_round_brief_kind"):
        op.drop_constraint(name, "rounds", type_="check")
    op.drop_constraint("rounds_firm_id_amounts_checked_by_fkey", "rounds", type_="foreignkey")
    op.drop_constraint("rounds_firm_id_copied_from_round_id_fkey", "rounds", type_="foreignkey")
    for col in ("amounts_checked_at", "amounts_checked_by", "claim_value", "provisional_execution", "joint_liability",
                "statement_variant", "statement_text", "narrative", "copied_from_round_id", "failed_stage",
                "brief_kind"):
        op.drop_column("rounds", col)
