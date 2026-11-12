"""The owner is decided by OWNER_EMAIL when it is needed, so users.is_owner goes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('users', 'is_owner')


def downgrade() -> None:
    op.add_column('users', sa.Column('is_owner', sa.Boolean(), server_default=sa.false(), nullable=False))
