"""Content plan: reels the user plans to film, with a status.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reels", sa.Column("plan_status", sa.String(length=20), nullable=True))
    op.add_column("reels", sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reels", sa.Column("plan_status_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_reels_plan_status"), "reels", ["plan_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reels_plan_status"), table_name="reels")
    op.drop_column("reels", "plan_status_at")
    op.drop_column("reels", "planned_at")
    op.drop_column("reels", "plan_status")
