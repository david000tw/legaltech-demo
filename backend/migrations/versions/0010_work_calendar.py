"""行政機關辦公日曆表（全系統共用、app_user 唯讀），期限試算的假日順延用

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_calendar",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("is_holiday", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )
    op.execute("GRANT SELECT ON work_calendar TO app_user")


def downgrade() -> None:
    op.drop_table("work_calendar")
