"""貼司法院網址加入判決：corpus_writer 帳號（只能讀、新增判決與段落）、判決來源代碼、加入紀錄表（RLS）

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-24
"""
import re
from urllib.parse import parse_qs, unquote, urlsplit

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
_FILES = re.compile(r"/FILES/([A-Z]{4})/([^/]+)\.pdf$")


def _source_key(url: str) -> str | None:
    """凍結在 migration 裡的小解析器（不 import app）：司法院網址 → 「代碼,年,字別,號,yyyymmdd,序號」。"""
    try:
        u = urlsplit(url)
        m = _FILES.search(unquote(u.path))
        if m:
            raw = f"{m.group(1)},{m.group(2)}"
        else:
            ids = parse_qs(u.query).get("id")
            if not ids:
                return None
            raw = ids[0]
        code, year, word, number, ymd, seq = (x.strip() for x in raw.split(","))
        return f"{code},{int(year)},{word},{int(number)},{int(ymd):08d},{int(seq)}"
    except ValueError:
        return None


def upgrade() -> None:
    # 帳號是整台資料庫伺服器共用的（開發與測試資料庫都用它），不存在才建立
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'corpus_writer') THEN
                CREATE ROLE corpus_writer LOGIN PASSWORD 'corpuspass'
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END $$""")
    op.execute("GRANT USAGE ON SCHEMA public TO corpus_writer")
    op.execute("GRANT SELECT, INSERT ON judgments, judgment_passages TO corpus_writer")

    op.add_column("judgments", sa.Column("source_key", sa.String(length=100), nullable=True))
    conn = op.get_bind()
    for jid, url in conn.execute(sa.text("select id, source_url from judgments")).all():
        key = _source_key(url)
        if key:
            conn.execute(sa.text("update judgments set source_key = :k where id = :i"), {"k": key, "i": jid})
    op.create_unique_constraint("uq_judgment_source_key", "judgments", ["source_key"])

    op.create_table(
        "judgment_imports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("firm_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=True),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("judgment_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        sa.ForeignKeyConstraint(["firm_id", "case_id"], ["cases.firm_id", "cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["firm_id", "member_id"], ["members.firm_id", "members.id"]),
        sa.ForeignKeyConstraint(["judgment_id"], ["judgments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE judgment_imports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE judgment_imports FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON judgment_imports "
               f"USING (firm_id = {FIRM_EXPR}) WITH CHECK (firm_id = {FIRM_EXPR})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON judgment_imports TO app_user")


def downgrade() -> None:
    op.drop_table("judgment_imports")
    op.drop_constraint("uq_judgment_source_key", "judgments", type_="unique")
    op.drop_column("judgments", "source_key")
    # 帳號可能還有其他資料庫在用：只撤權限、不刪帳號
    op.execute("REVOKE ALL ON judgments, judgment_passages FROM corpus_writer")
    op.execute("REVOKE USAGE ON SCHEMA public FROM corpus_writer")
