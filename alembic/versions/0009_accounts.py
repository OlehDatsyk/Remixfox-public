"""Accounts: users, sign-in sessions, Codex's stored sign-in; competitors belong to a user.

Two users may track the same Instagram account, so usernames are unique per
user and shortcodes per competitor.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('codex_auth',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('data', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_codex_auth'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('is_owner', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('stripe_customer_id', sa.String(length=100), nullable=True),
    sa.Column('subscription_status', sa.String(length=30), nullable=True),
    sa.Column('subscription_renews_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_index(op.f('ix_users_stripe_customer_id'), 'users', ['stripe_customer_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.add_column('competitors', sa.Column('user_id', sa.Integer(), nullable=True))
    op.drop_constraint(op.f('uq_competitors_username'), 'competitors', type_='unique')
    op.create_index(op.f('ix_competitors_user_id'), 'competitors', ['user_id'], unique=False)
    op.create_unique_constraint(op.f('uq_competitors_user_id'), 'competitors', ['user_id', 'username'])
    op.create_foreign_key(op.f('fk_competitors_user_id_users'), 'competitors', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint(op.f('uq_reels_shortcode'), 'reels', type_='unique')
    op.create_unique_constraint(op.f('uq_reels_competitor_id'), 'reels', ['competitor_id', 'shortcode'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_reels_competitor_id'), 'reels', type_='unique')
    op.create_unique_constraint(op.f('uq_reels_shortcode'), 'reels', ['shortcode'])
    op.drop_constraint(op.f('fk_competitors_user_id_users'), 'competitors', type_='foreignkey')
    op.drop_constraint(op.f('uq_competitors_user_id'), 'competitors', type_='unique')
    op.drop_index(op.f('ix_competitors_user_id'), table_name='competitors')
    op.create_unique_constraint(op.f('uq_competitors_username'), 'competitors', ['username'])
    op.drop_column('competitors', 'user_id')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_users_stripe_customer_id'), table_name='users')
    op.drop_table('users')
    op.drop_table('codex_auth')
