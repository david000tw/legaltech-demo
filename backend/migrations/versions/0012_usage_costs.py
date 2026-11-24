"""用量紀錄加成功／失敗與 5 分鐘、1 小時快取寫入；AI 單價表與匯率表（皆 RLS）

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
TABLES = ("ai_prices", "cost_settings")


def _usd(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(12, 4), nullable=False)


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("cache_write_5m_tokens", sa.Integer(), nullable=True))
    op.add_column("usage_records", sa.Column("cache_write_1h_tokens", sa.Integer(), nullable=True))
    op.add_column("usage_records", sa.Column("result", sa.String(length=10), server_default="ok", nullable=False))
    op.create_check_constraint("ck_usage_result", "usage_records", "result in ('ok', 'failed')")
    op.create_table(
        "ai_prices",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        _usd("input_usd"), _usd("output_usd"), _usd("cache_read_usd"), _usd("cache_write_5m_usd"),
        _usd("cache_write_1h_usd"),
        sa.Column("billing", sa.String(length=10), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.CheckConstraint("billing in ('metered', 'unmetered')", name="ck_ai_price_billing"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("firm_id", "source", "model", name="uq_ai_price"),
    )
    op.create_table(
        "cost_settings",
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("usd_twd", sa.Numeric(10, 4), nullable=False),
        sa.Column("rate_set_on", sa.Date(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.PrimaryKeyConstraint("firm_id"),
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
    op.drop_constraint("ck_usage_result", "usage_records", type_="check")
    op.drop_column("usage_records", "result")
    op.drop_column("usage_records", "cache_write_1h_tokens")
    op.drop_column("usage_records", "cache_write_5m_tokens")
