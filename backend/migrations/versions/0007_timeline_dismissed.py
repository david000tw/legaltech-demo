"""大事記：律師刪掉的 AI 事件改成隱藏（origin='dismissed'），重新整理時同一句原文不再找回來

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_timeline_origin", "timeline_events", type_="check")
    op.create_check_constraint("ck_timeline_origin", "timeline_events", "origin in ('ai', 'lawyer', 'dismissed')")


def downgrade() -> None:
    op.execute("delete from timeline_events where origin = 'dismissed'")
    op.drop_constraint("ck_timeline_origin", "timeline_events", type_="check")
    op.create_check_constraint("ck_timeline_origin", "timeline_events", "origin in ('ai', 'lawyer')")
