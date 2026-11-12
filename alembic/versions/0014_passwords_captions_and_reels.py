"""Passwords, translated captions, reels per month; Polish is no longer offered.

users.password_hash holds the optional password (scrypt); reels.caption_translation
the caption in the user's language; usage_events.units the reels a fetch brought
(kind "reels"), for the monthly limit. Accounts that chose Polish go back to
British English. Reels already processed go back to the AI once, so their captions
are translated too (the users' own texts are kept).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (autogenerate may refer to sqlmodel types)


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reels', sa.Column('caption_translation', sa.Text(), nullable=True))
    op.add_column('usage_events', sa.Column('units', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=True))
    op.execute("UPDATE users SET translate_to = 'en' WHERE translate_to NOT IN ('en', 'es', 'fr', 'de', 'it', 'pt', 'uk')")
    op.execute(
        "UPDATE reels SET translated_at = NULL, translation_started_at = NULL, translation_error = NULL"
        " WHERE transcript IS NOT NULL AND (transcript <> '' OR caption <> '')"
    )


def downgrade() -> None:
    op.drop_column('users', 'password_hash')
    op.drop_column('usage_events', 'units')
    op.drop_column('reels', 'caption_translation')
