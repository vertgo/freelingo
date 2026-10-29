"""Store a learner's own encrypted Gemini API key.

Revision ID: 0058_user_api_keys
Revises: 0057_flashcard_review_receipts
"""

import sqlalchemy as sa

from alembic import op

revision = "0058_user_api_keys"
down_revision = "0057_flashcard_review_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("encrypted_api_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "encrypted_api_key")
