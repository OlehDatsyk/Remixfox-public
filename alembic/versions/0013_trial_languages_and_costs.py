"""The AI's language per account, costs in usage_events, and another try for rejected reels.

users.translate_to is the language the AI writes in (British English unless the
user picks another in Settings); reels.translated_into is the language of the
AI's version. usage_events gets cost_usd and tokens for the admin panel; the
events so far get the usual estimates (a fetch about $0.053, a transcript about
$0.05). Reels the AI rejected under the old 15% length rule (translations from
languages such as Ukrainian, which need more English words) go back to the queue.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401  (autogenerate may refer to sqlmodel types)


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reels', sa.Column('translated_into', sqlmodel.sql.sqltypes.AutoString(length=8), nullable=True))
    op.add_column('usage_events', sa.Column('cost_usd', sa.Float(), server_default='0', nullable=False))
    op.add_column('usage_events', sa.Column('tokens', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('translate_to', sqlmodel.sql.sqltypes.AutoString(length=8), server_default='en', nullable=False))
    op.execute("UPDATE reels SET translated_into = 'en' WHERE translated_at IS NOT NULL")
    op.execute("UPDATE usage_events SET cost_usd = 0.053 WHERE kind = 'fetch'")
    op.execute("UPDATE usage_events SET cost_usd = 0.051 WHERE kind = 'video'")
    op.execute(
        "UPDATE reels SET translation_error = NULL, translation_started_at = NULL"
        " WHERE translation_error IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column('users', 'translate_to')
    op.drop_column('usage_events', 'tokens')
    op.drop_column('usage_events', 'cost_usd')
    op.drop_column('reels', 'translated_into')
