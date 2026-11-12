"""Initial schema: competitors, reels, fetch jobs and stored files.

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('competitors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=30), nullable=False),
    sa.Column('is_demo', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competitors')),
    sa.UniqueConstraint('username', name=op.f('uq_competitors_username'))
    )
    op.create_table('fetch_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('reels_saved', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], name=op.f('fk_fetch_jobs_competitor_id_competitors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fetch_jobs'))
    )
    op.create_index(op.f('ix_fetch_jobs_competitor_id'), 'fetch_jobs', ['competitor_id'], unique=False)
    op.create_table('reels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('competitor_id', sa.Integer(), nullable=False),
    sa.Column('shortcode', sa.String(length=64), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=True),
    sa.Column('caption', sa.Text(), nullable=False),
    sa.Column('cover_url', sa.String(length=500), nullable=True),
    sa.Column('video_url', sa.Text(), nullable=True),
    sa.Column('views', sa.BigInteger(), nullable=False),
    sa.Column('likes', sa.BigInteger(), nullable=False),
    sa.Column('comments', sa.BigInteger(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('hook', sa.Text(), nullable=False),
    sa.Column('script', sa.Text(), nullable=False),
    sa.Column('cta', sa.Text(), nullable=False),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id'], name=op.f('fk_reels_competitor_id_competitors'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reels')),
    sa.UniqueConstraint('shortcode', name=op.f('uq_reels_shortcode'))
    )
    op.create_index(op.f('ix_reels_competitor_id'), 'reels', ['competitor_id'], unique=False)
    op.create_index(op.f('ix_reels_posted_at'), 'reels', ['posted_at'], unique=False)
    op.create_table('stored_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reel_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['reel_id'], ['reels.id'], name=op.f('fk_stored_files_reel_id_reels'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stored_files')),
    sa.UniqueConstraint('reel_id', 'kind', name=op.f('uq_stored_files_reel_id'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('stored_files')
    op.drop_index(op.f('ix_reels_posted_at'), table_name='reels')
    op.drop_index(op.f('ix_reels_competitor_id'), table_name='reels')
    op.drop_table('reels')
    op.drop_index(op.f('ix_fetch_jobs_competitor_id'), table_name='fetch_jobs')
    op.drop_table('fetch_jobs')
    op.drop_table('competitors')
