"""Pipeline tracking and language detection.

- job_events: each fetch job's step-by-step log, shown in the activity panel.
- fetch_jobs: the reels each job added and transcribed.
- reels: the transcript's language, and when the Codex worker took it.
- Sends reels processed before language detection back to Codex, so English
  reels are kept word for word instead of being respelt.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["fetch_jobs.id"], name=op.f("fk_job_events_job_id_fetch_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
    )
    op.create_index(op.f("ix_job_events_job_id"), "job_events", ["job_id"], unique=False)
    for name in ("new_reel_ids", "transcribed_reel_ids"):
        op.add_column("fetch_jobs", sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        op.alter_column("fetch_jobs", name, server_default=None)
    op.add_column("reels", sa.Column("transcript_language", sa.String(length=16), nullable=True))
    op.add_column("reels", sa.Column("translation_started_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        "UPDATE reels SET translation_hook = NULL, translation_script = NULL, translation_cta = NULL,"
        " translation_model = NULL, translated_at = NULL, translation_error = NULL"
        " WHERE transcript_language IS NULL AND (translated_at IS NOT NULL OR translation_error IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_column("reels", "translation_started_at")
    op.drop_column("reels", "transcript_language")
    op.drop_column("fetch_jobs", "transcribed_reel_ids")
    op.drop_column("fetch_jobs", "new_reel_ids")
    op.drop_index(op.f("ix_job_events_job_id"), table_name="job_events")
    op.drop_table("job_events")
