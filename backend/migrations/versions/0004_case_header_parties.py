"""狀頭與當事人：案件狀頭欄位與訴訟代理人、當事人表（RLS）、對造書狀的遞狀人

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
ROLES = ("原告", "被告", "上訴人", "被上訴人", "反訴原告", "反訴被告", "聲請人", "相對人")  # 凍結當時的清單
ALIGNMENTS = ("ours", "opposing", "aligned")


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.add_column("cases", sa.Column("court", sa.String(length=100), nullable=True))
    op.add_column("cases", sa.Column("case_number", sa.String(length=100), nullable=True))
    op.add_column("cases", sa.Column("division", sa.String(length=50), nullable=True))
    op.add_column("cases", sa.Column("cause", sa.String(length=200), nullable=True))
    op.add_column("cases", sa.Column("addressee", sa.String(length=200), nullable=True))
    op.add_column("cases", sa.Column("attorneys", postgresql.JSONB(astext_type=sa.Text()),
                                     server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("cases", sa.Column("reference_date", sa.Date(), nullable=True))
    # 計畫 1 的舊案件：訴訟代理人帶入建立案件的律師（與新建案件的預設相同；助理建立的維持空白）。
    # 結構檔以資料庫管理者身分執行，不受 RLS 限制。
    op.execute("""
        UPDATE cases c SET attorneys = jsonb_build_array(
            CASE WHEN m.display_name LIKE '%律師' THEN m.display_name ELSE m.display_name || '律師' END)
        FROM members m
        WHERE m.id = c.created_by AND m.role = 'lawyer' AND c.attorneys = '[]'::jsonb
    """)

    op.create_table(
        "case_parties",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("alignment", sa.String(length=20), nullable=False),
        sa.Column("legal_rep", sa.String(length=200), nullable=True),
        sa.Column("position_note", sa.Text(), nullable=True),
        sa.CheckConstraint(f"role in ({_in(ROLES)})", name="ck_party_role"),
        sa.CheckConstraint(f"alignment in ({_in(ALIGNMENTS)})", name="ck_party_alignment"),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.ForeignKeyConstraint(["firm_id", "case_id"], ["cases.firm_id", "cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("firm_id", "id", name="uq_case_parties_firm_id"),
    )
    op.execute("ALTER TABLE case_parties ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_parties FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON case_parties "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON case_parties TO app_user")

    op.add_column("documents", sa.Column("filed_by_party_id", sa.UUID(), nullable=True))
    op.create_foreign_key("documents_firm_id_filed_by_party_id_fkey", "documents", "case_parties",
                          ["firm_id", "filed_by_party_id"], ["firm_id", "id"],
                          ondelete="SET NULL (filed_by_party_id)")


def downgrade() -> None:
    op.drop_constraint("documents_firm_id_filed_by_party_id_fkey", "documents", type_="foreignkey")
    op.drop_column("documents", "filed_by_party_id")
    op.drop_table("case_parties")
    for col in ("reference_date", "attorneys", "addressee", "cause", "division", "case_number", "court"):
        op.drop_column("cases", col)
