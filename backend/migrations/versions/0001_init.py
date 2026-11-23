"""init: 全部資料表、row-level security、權限、登入查詢函式

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""
from alembic import op

from app.models import PUBLIC_TABLES, Base, business_tables

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"


def upgrade() -> None:
    bind = op.get_bind()
    # 計畫 1 期間尚無部署，初始遷移直接依目前模型建表；之後的變更一律寫明確的遷移。
    Base.metadata.create_all(bind)

    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    for t in business_tables():
        col = "id" if t == "firms" else "firm_id"
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({col} = {FIRM_EXPR}) WITH CHECK ({col} = {FIRM_EXPR})"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO app_user")
    for t in PUBLIC_TABLES:
        op.execute(f"GRANT SELECT ON {t} TO app_user")

    op.execute(
        """
        CREATE FUNCTION auth_lookup(p_email text)
        RETURNS TABLE(member_id uuid, firm_id uuid, password_hash text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
            SELECT m.id, m.firm_id, m.password_hash
            FROM members m WHERE lower(m.email) = lower(p_email)
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_lookup(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup(text) TO app_user")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_lookup(text)")
    Base.metadata.drop_all(op.get_bind())
