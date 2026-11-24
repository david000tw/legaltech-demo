"""卷證說明、大事記、書狀健檢：AI 工作表、三張結果表（皆 RLS），卷證的說明指紋欄位

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
TABLES = ("ai_jobs", "evidence_notes", "timeline_events", "health_reports")
# 凍結當時的清單
STEPS = ("evidence_notes", "timeline", "health")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _check(col: str, values, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{col} in ({_in(values)})", name=name)


def _tfk(col: str, table: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["firm_id", col], [f"{table}.firm_id", f"{table}.id"], ondelete=ondelete)


def _common() -> list:
    return [sa.Column("id", sa.UUID(), nullable=False), sa.Column("firm_id", sa.UUID(), nullable=False)]


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _jsonb_list(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"),
                     nullable=False)


def upgrade() -> None:
    op.add_column("documents", sa.Column("notes_fingerprint", sa.String(length=64), nullable=True))

    op.create_table(
        "ai_jobs", *_common(),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=True),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=True),
        _now("started_at"), _now("heartbeat_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _check("step", STEPS, "ck_ai_job_step"),
        _check("status", ("running", "done", "failed"), "ck_ai_job_status"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        _tfk("case_id", "cases", "CASCADE"), _tfk("round_id", "rounds", "CASCADE"), _tfk("member_id", "members"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "evidence_notes", *_common(),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        _jsonb_list("reasons"),
        sa.Column("origin", sa.String(length=10), nullable=False),
        _now("created_at"),
        _check("kind", ("proves", "caution"), "ck_evidence_note_kind"),
        _check("status", ("found", "not_found", "absence"), "ck_evidence_note_status"),
        _check("origin", ("ai", "lawyer"), "ck_evidence_note_origin"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        _tfk("case_id", "cases", "CASCADE"), _tfk("document_id", "documents", "CASCADE"),
        _tfk("page_id", "pages", "SET NULL (page_id)"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "timeline_events", *_common(),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("date_precision", sa.String(length=10), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=True),
        sa.Column("page_id", sa.UUID(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=10), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        _jsonb_list("reasons"),
        _now("created_at"),
        _check("date_precision", ("day", "month"), "ck_timeline_date_precision"),
        _check("source_kind", ("evidence", "opponent"), "ck_timeline_source_kind"),
        _check("origin", ("ai", "lawyer"), "ck_timeline_origin"),
        _check("status", ("found", "not_found", "unchecked"), "ck_timeline_status"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        _tfk("case_id", "cases", "CASCADE"), _tfk("page_id", "pages", "SET NULL (page_id)"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "health_reports", *_common(),
        sa.Column("round_id", sa.UUID(), nullable=False),
        _now("created_at"),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        _jsonb_list("items"),
        sa.Column("undisputed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _jsonb_list("issues"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        _tfk("round_id", "rounds", "CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", name="uq_health_report_round"),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_column("documents", "notes_fingerprint")
