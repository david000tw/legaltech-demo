"""init: 全部資料表、row-level security、權限、登入查詢函式

Revision ID: 0001
Revises:
Create Date: 2026-11-23

結構定義寫死在本檔（計畫 1B 凍結），之後模型變動一律寫新的結構檔，不得回頭改這裡。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

FIRM_EXPR = "nullif(current_setting('app.firm_id', true), '')::uuid"
# 有 firm_id 的業務表（firms 本身以 id 判定）：一律 RLS。
BUSINESS_TABLES = ('firms', 'members', 'cases', 'rounds', 'usage_records', 'documents', 'drafts', 'exhibits', 'export_records', 'pages', 'paragraphs', 'claims', 'page_texts', 'review_events', 'candidates', 'citations')
# 合規門檻欄位（is_internal_test、bar_verified、role）所在的表：一般請求用的帳號只能讀。
READ_ONLY_FOR_APP = ("firms", "members")
# 公開資料（判決、法條）：不分事務所，一般請求用的帳號只能讀。
PUBLIC_TABLES = ("judgments", "judgment_passages", "statutes", "statute_articles")
DROP_ORDER = ('citations', 'candidates', 'review_events', 'page_texts', 'claims', 'paragraphs', 'pages', 'export_records', 'exhibits', 'drafts', 'documents', 'usage_records', 'rounds', 'cases', 'statute_articles', 'members', 'judgment_passages', 'statutes', 'judgments', 'firms')


def upgrade() -> None:
    op.create_table('firms',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('is_internal_test', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('judgments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('citation', sa.String(length=300), nullable=False),
    sa.Column('court', sa.String(length=100), nullable=False),
    sa.Column('court_level', sa.String(length=20), nullable=False),
    sa.Column('decided_on', sa.Date(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('full_text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('curated', sa.Boolean(), nullable=False),
    sa.CheckConstraint("court_level in ('supreme', 'high', 'district', 'summary')", name='ck_judgment_level'),
    sa.CheckConstraint("status in ('active', 'withdrawn', 'corrected')", name='ck_judgment_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('citation')
    )
    op.create_table('statutes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('pcode', sa.String(length=20), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('judgment_passages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('judgment_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('passage_type', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.CheckConstraint("passage_type in ('main_text', 'party_claim', 'court_reasoning', 'other')", name='ck_passage_type'),
    sa.CheckConstraint("status in ('active', 'withdrawn', 'corrected')", name='ck_passage_status'),
    sa.ForeignKeyConstraint(['judgment_id'], ['judgments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('members',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('bar_number', sa.String(length=50), nullable=True),
    sa.Column('bar_verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role in ('lawyer', 'assistant', 'firm_admin')", name='ck_member_role'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_members_firm_id')
    )
    op.create_index('uq_members_email_lower', 'members', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('statute_articles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('statute_id', sa.UUID(), nullable=False),
    sa.Column('article_no', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('amended_on', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['statute_id'], ['statutes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('statute_id', 'article_no', name='uq_statute_article')
    )
    op.create_table('cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('our_side', sa.String(length=20), nullable=False),
    sa.Column('case_type', sa.String(length=50), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("our_side in ('plaintiff', 'defendant')", name='ck_case_side'),
    sa.ForeignKeyConstraint(['firm_id', 'created_by'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_cases_firm_id')
    )
    op.create_table('rounds',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status in ('created', 'running', 'drafted', 'failed')", name='ck_round_status'),
    sa.ForeignKeyConstraint(['firm_id', 'case_id'], ['cases.firm_id', 'cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'seq', name='uq_round_case_seq'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_rounds_firm_id')
    )
    op.create_table('usage_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=True),
    sa.Column('member_id', sa.UUID(), nullable=True),
    sa.Column('step', sa.String(length=50), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['firm_id', 'case_id'], ['cases.firm_id', 'cases.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'member_id'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('filename', sa.String(length=500), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind in ('evidence', 'opponent_brief')", name='ck_document_kind'),
    sa.ForeignKeyConstraint(['firm_id', 'case_id'], ['cases.firm_id', 'cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id', 'round_id'], ['rounds.firm_id', 'rounds.id'], ),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_documents_firm_id')
    )
    op.create_table('drafts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['firm_id', 'round_id'], ['rounds.firm_id', 'rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_drafts_firm_id')
    )
    op.create_table('exhibits',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('prefix', sa.String(length=10), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.CheckConstraint("prefix in ('原證', '被證')", name='ck_exhibit_prefix'),
    sa.ForeignKeyConstraint(['firm_id', 'case_id'], ['cases.firm_id', 'cases.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id', 'document_id'], ['documents.firm_id', 'documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('case_id', 'prefix', 'number', name='uq_exhibit_number')
    )
    op.create_table('export_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('draft_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('bar_number', sa.String(length=50), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['firm_id', 'draft_id'], ['drafts.firm_id', 'drafts.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'member_id'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('pages',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('page_no', sa.Integer(), nullable=False),
    sa.Column('needs_ocr', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['firm_id', 'document_id'], ['documents.firm_id', 'documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'page_no', name='uq_page_doc_no'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_pages_firm_id')
    )
    op.create_table('paragraphs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('draft_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('heading', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('verify_reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_bar_number', sa.String(length=50), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status in ('unreviewed', 'reviewed')", name='ck_paragraph_status'),
    sa.ForeignKeyConstraint(['firm_id', 'draft_id'], ['drafts.firm_id', 'drafts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id', 'reviewed_by'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_paragraphs_firm_id')
    )
    op.create_table('claims',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=True),
    sa.Column('quote_verified', sa.Boolean(), nullable=False),
    sa.Column('selection', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['firm_id', 'page_id'], ['pages.firm_id', 'pages.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'round_id'], ['rounds.firm_id', 'rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('page_texts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('page_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('verified_against_image', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source in ('text_layer', 'ai_ocr', 'manual')", name='ck_page_text_source'),
    sa.ForeignKeyConstraint(['firm_id', 'page_id'], ['pages.firm_id', 'pages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('firm_id', 'id', name='uq_page_texts_firm_id'),
    sa.UniqueConstraint('page_id', 'version', name='uq_page_text_version')
    )
    op.create_table('review_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('paragraph_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('before_text', sa.Text(), nullable=True),
    sa.Column('after_text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action in ('edit', 'rewrite', 'confirm_citation', 'mark_reviewed', 'revert')", name='ck_review_action'),
    sa.ForeignKeyConstraint(['firm_id', 'member_id'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'paragraph_id'], ['paragraphs.firm_id', 'paragraphs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('candidates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('page_text_id', sa.UUID(), nullable=True),
    sa.Column('judgment_passage_id', sa.UUID(), nullable=True),
    sa.Column('statute_article_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("kind in ('evidence', 'judgment', 'statute')", name='ck_candidate_kind'),
    sa.ForeignKeyConstraint(['firm_id', 'page_text_id'], ['page_texts.firm_id', 'page_texts.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'round_id'], ['rounds.firm_id', 'rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.ForeignKeyConstraint(['judgment_passage_id'], ['judgment_passages.id'], ),
    sa.ForeignKeyConstraint(['statute_article_id'], ['statute_articles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('round_id', 'code', name='uq_candidate_code')
    )
    op.create_table('citations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('firm_id', sa.UUID(), nullable=False),
    sa.Column('paragraph_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('page_text_id', sa.UUID(), nullable=True),
    sa.Column('judgment_passage_id', sa.UUID(), nullable=True),
    sa.Column('statute_article_id', sa.UUID(), nullable=True),
    sa.Column('verify_status', sa.String(length=10), nullable=False),
    sa.Column('verify_reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('lawyer_confirmed', sa.Boolean(), nullable=False),
    sa.Column('confirmed_by', sa.UUID(), nullable=True),
    sa.CheckConstraint("kind in ('evidence', 'judgment', 'statute')", name='ck_citation_kind'),
    sa.CheckConstraint("verify_status in ('ok', 'failed')", name='ck_citation_status'),
    sa.ForeignKeyConstraint(['firm_id', 'confirmed_by'], ['members.firm_id', 'members.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'page_text_id'], ['page_texts.firm_id', 'page_texts.id'], ),
    sa.ForeignKeyConstraint(['firm_id', 'paragraph_id'], ['paragraphs.firm_id', 'paragraphs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['firm_id'], ['firms.id'], ),
    sa.ForeignKeyConstraint(['judgment_passage_id'], ['judgment_passages.id'], ),
    sa.ForeignKeyConstraint(['statute_article_id'], ['statute_articles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    for t in BUSINESS_TABLES:
        col = "id" if t == "firms" else "firm_id"
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({col} = {FIRM_EXPR}) WITH CHECK ({col} = {FIRM_EXPR})"
        )
        privileges = "SELECT" if t in READ_ONLY_FOR_APP else "SELECT, INSERT, UPDATE, DELETE"
        op.execute(f"GRANT {privileges} ON {t} TO app_user")
    for t in PUBLIC_TABLES:
        op.execute(f"GRANT SELECT ON {t} TO app_user")

    op.execute(
        """
        CREATE FUNCTION auth_lookup(p_email text)
        RETURNS TABLE(member_id uuid, firm_id uuid, password_hash text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
            SELECT m.id, m.firm_id, m.password_hash
            FROM public.members m WHERE lower(m.email) = lower(p_email)
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_lookup(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup(text) TO app_user")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_lookup(text)")
    for t in DROP_ORDER:
        op.drop_table(t)
