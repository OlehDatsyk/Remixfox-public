"""Passwordless sign-in: one-time e-mail codes and the Telegram bot.

Users lose their passwords and may have no e-mail (Telegram-only accounts);
they gain a Telegram id, @username and name. New tables: login_codes,
telegram_logins and avatars (profile photos from Telegram).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (autogenerate may refer to sqlmodel types)


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('login_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sqlmodel.sql.sqltypes.AutoString(length=254), nullable=False),
    sa.Column('code_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('ip', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_codes'))
    )
    op.create_index(op.f('ix_login_codes_email'), 'login_codes', ['email'], unique=False)
    op.create_index(op.f('ix_login_codes_ip'), 'login_codes', ['ip'], unique=False)
    op.create_table('avatars',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_avatars_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_avatars'))
    )
    op.create_table('telegram_logins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('browser_hash', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.Column('connect_user_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['connect_user_id'], ['users.id'], name=op.f('fk_telegram_logins_connect_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_telegram_logins_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_logins')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_telegram_logins_token_hash'))
    )
    op.add_column('users', sa.Column('telegram_id', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('telegram_username', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
    op.add_column('users', sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True))
    op.alter_column('users', 'email',
               existing_type=sa.VARCHAR(length=254),
               nullable=True)
    op.create_unique_constraint(op.f('uq_users_telegram_id'), 'users', ['telegram_id'])
    op.drop_column('users', 'password_hash')


def downgrade() -> None:
    # Passwords cannot come back: accounts get an empty one (no sign-in by password).
    op.add_column('users', sa.Column('password_hash', sa.VARCHAR(length=200), server_default='', nullable=False))
    op.drop_constraint(op.f('uq_users_telegram_id'), 'users', type_='unique')
    op.alter_column('users', 'email',
               existing_type=sa.VARCHAR(length=254),
               nullable=False)
    op.drop_column('users', 'name')
    op.drop_column('users', 'telegram_username')
    op.drop_column('users', 'telegram_id')
    op.drop_table('telegram_logins')
    op.drop_table('avatars')
    op.drop_index(op.f('ix_login_codes_ip'), table_name='login_codes')
    op.drop_index(op.f('ix_login_codes_email'), table_name='login_codes')
    op.drop_table('login_codes')
