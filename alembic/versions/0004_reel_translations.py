"""Reel translations: the transcript in British English, split by Codex CLI.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEXT_COLUMNS = ("translation_hook", "translation_script", "translation_cta", "translation_error")


def upgrade() -> None:
    for name in TEXT_COLUMNS[:3]:
        op.add_column("reels", sa.Column(name, sa.Text(), nullable=True))
    op.add_column("reels", sa.Column("translation_model", sa.String(length=100), nullable=True))
    op.add_column("reels", sa.Column("translated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reels", sa.Column("translation_error", sa.Text(), nullable=True))


def downgrade() -> None:
    for name in ("translated_at", "translation_model", *TEXT_COLUMNS):
        op.drop_column("reels", name)
