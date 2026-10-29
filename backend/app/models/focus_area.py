from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FocusArea(Base):
    """A mistake the learner keeps making, found by reviewing their conversations."""

    __tablename__ = "focus_areas"
    __table_args__ = (
        UniqueConstraint("user_id", "target_language", "concept_key", name="uq_focus_area_concept"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_language: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    # Short label in the learner's native language, e.g. "Ser vs. estar".
    concept: Mapped[str] = mapped_column(String(120), nullable=False)
    # Lowercased, whitespace-collapsed concept used to merge repeats.
    concept_key: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="grammar")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Most recent first: [{"said": ..., "correction": ...}], at most MAX_EXAMPLES.
    examples: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Conversations (or chat segments) in which the mistake was seen.
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Correct uses (conversation or drill) since the mistake was last seen.
    correct_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # active | mastered | dismissed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    mastered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
