"""Track recurring learner mistakes found in conversations.

Revision ID: 0059_focus_areas
Revises: 0058_user_api_keys
"""

import sqlalchemy as sa

from alembic import op

revision = "0059_focus_areas"
down_revision = "0058_user_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "focus_areas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_language", sa.String(10), nullable=False),
        sa.Column("concept", sa.String(120), nullable=False),
        sa.Column("concept_key", sa.String(120), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("correct_streak", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("mastered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "target_language", "concept_key", name="uq_focus_area_concept"
        ),
    )
    op.create_index("ix_focus_areas_user_id", "focus_areas", ["user_id"])
    op.create_index("ix_focus_areas_target_language", "focus_areas", ["target_language"])
    op.create_index("ix_focus_areas_status", "focus_areas", ["status"])
    op.add_column(
        "conversations", sa.Column("focus_reviewed_through_id", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversations", "focus_reviewed_through_id")
    op.drop_index("ix_focus_areas_status", table_name="focus_areas")
    op.drop_index("ix_focus_areas_target_language", table_name="focus_areas")
    op.drop_index("ix_focus_areas_user_id", table_name="focus_areas")
    op.drop_table("focus_areas")
