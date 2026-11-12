"""Plans and admins; the AI moves from Codex CLI to the OpenAI API.

Users get their Stripe subscription id and plan (start, pro or studio, monthly or
yearly); admins is the owner's list of up to 10 admin e-mails; usage_events logs
videos and fetches for the plan limits (filled from the reels and jobs so far);
codex_auth (the Codex CLI sign-in) goes, and pipeline events of the step "codex"
become "ai".

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (autogenerate may refer to sqlmodel types)
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=254), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admins')),
    sa.UniqueConstraint('email', name=op.f('uq_admins_email'))
    )
    op.create_table('usage_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_usage_events_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usage_events'))
    )
    op.create_index('ix_usage_events_user_id_kind_at', 'usage_events', ['user_id', 'kind', 'at'], unique=False)
    op.execute(
        "INSERT INTO usage_events (user_id, kind, at) SELECT c.user_id, 'video', r.transcript_fetched_at"
        " FROM reels r JOIN competitors c ON c.id = r.competitor_id"
        " WHERE c.user_id IS NOT NULL AND r.transcript_fetched_at IS NOT NULL"
    )
    op.execute(
        "INSERT INTO usage_events (user_id, kind, at) SELECT c.user_id, 'fetch', j.created_at"
        " FROM fetch_jobs j JOIN competitors c ON c.id = j.competitor_id WHERE c.user_id IS NOT NULL"
    )
    op.drop_table('codex_auth')
    op.add_column('users', sa.Column('stripe_subscription_id', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=True))
    op.add_column('users', sa.Column('plan', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True))
    op.add_column('users', sa.Column('plan_interval', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=True))
    op.execute("UPDATE job_events SET step = 'ai' WHERE step = 'codex'")


def downgrade() -> None:
    op.execute("UPDATE job_events SET step = 'codex' WHERE step = 'ai'")
    op.drop_column('users', 'plan_interval')
    op.drop_column('users', 'plan')
    op.drop_column('users', 'stripe_subscription_id')
    op.create_table('codex_auth',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('data', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_codex_auth'))
    )
    op.drop_index('ix_usage_events_user_id_kind_at', table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_table('admins')
