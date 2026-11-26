"""判決庫的條文索引、引用關係、要旨（計畫 3 丙）

三張全所共用的表（同 judgments，不開 RLS）：
- judgment_article_refs：法院理由段引用的條文；
- judgment_case_refs：法院理由段以「…意旨參照」等方式引用的判決；
- judgment_summaries：節錄式要旨，每份判決一列。
judgments 加自己的比對鍵 case_key（舊資料在這裡用 SQL 補）與規則版本 refs_version（由 reindex-judgments 補）；
candidates 加 via_article（2B 依條文多找進來的判決段）。
corpus_writer 可以讀、新增三張表；要旨表另外只能改狀態與結果欄位（欄位層級授權）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# 與 app/corpus/case_refs.py 的 judgment_key 同一個規則（測試會對所有種子判決比對兩邊結果）
CASE_KEY_SQL = r"""
UPDATE judgments SET case_key = replace(r.m[1] || '|' || (r.m[2])::int || '|' || r.m[3] || '|' || (r.m[4])::int,
                                        '臺', '台')
FROM (SELECT id, regexp_match(citation, '^(\D+?)(\d+)年度?(\D+?)字第(\d+)號') AS m FROM judgments) AS r
WHERE judgments.id = r.id AND r.m IS NOT NULL
"""
SUMMARY_UPDATABLE = "status, claim_token, claimed_at, fingerprint, issues, excerpts, model, finished_at, error_kind"
TABLES = "judgment_article_refs, judgment_case_refs, judgment_summaries"


def _refs(name: str, cols: list[sa.Column], unique: tuple[str, ...], uq_name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("judgment_id", sa.UUID(), nullable=False),
        sa.Column("passage_id", sa.UUID(), nullable=False),
        *cols,
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["judgment_id"], ["judgments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["judgment_passages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*unique, name=uq_name),
    )
    op.create_index(f"ix_{name}_judgment", name, ["judgment_id"])


def upgrade() -> None:
    op.add_column("judgments", sa.Column("case_key", sa.String(length=120), nullable=True))
    op.add_column("judgments", sa.Column("refs_version", sa.Integer(), nullable=True))
    op.create_index("ix_judgments_case_key", "judgments", ["case_key"])
    op.add_column("candidates", sa.Column("via_article", sa.String(length=100), nullable=True))

    _refs("judgment_article_refs", [sa.Column("law", sa.String(length=50), nullable=False),
                                    sa.Column("article_no", sa.String(length=20), nullable=False)],
          ("passage_id", "law", "article_no"), "uq_judgment_article_ref")
    op.create_index("ix_judgment_article_refs_law_article", "judgment_article_refs", ["law", "article_no"])
    _refs("judgment_case_refs", [sa.Column("key", sa.String(length=120), nullable=False),
                                 sa.Column("display", sa.String(length=200), nullable=False)],
          ("passage_id", "key"), "uq_judgment_case_ref")
    op.create_index("ix_judgment_case_refs_key", "judgment_case_refs", ["key"])

    op.create_table(
        "judgment_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("judgment_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("issues", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("excerpts", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_kind", sa.String(length=40), nullable=True),
        sa.CheckConstraint("status in ('generating', 'ok', 'failed')", name="ck_judgment_summary_status"),
        sa.ForeignKeyConstraint(["judgment_id"], ["judgments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("judgment_id", name="uq_judgment_summary_judgment"),
    )

    op.execute(CASE_KEY_SQL)
    op.execute(f"GRANT SELECT ON {TABLES} TO app_user")
    op.execute(f"GRANT SELECT, INSERT ON {TABLES} TO corpus_writer")
    op.execute(f"GRANT UPDATE ({SUMMARY_UPDATABLE}) ON judgment_summaries TO corpus_writer")


def downgrade() -> None:
    op.drop_table("judgment_summaries")
    op.drop_table("judgment_case_refs")
    op.drop_table("judgment_article_refs")
    op.drop_column("candidates", "via_article")
    op.drop_index("ix_judgments_case_key", table_name="judgments")
    op.drop_column("judgments", "refs_version")
    op.drop_column("judgments", "case_key")
