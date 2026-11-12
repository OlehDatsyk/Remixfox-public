"""Your voice for AI remixes: users.remix_profile (niche, audience, tone and offer).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('remix_profile', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'remix_profile')
