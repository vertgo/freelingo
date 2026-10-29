from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class FocusExample(BaseModel):
    said: str
    correction: str


class FocusAreaResponse(BaseModel):
    id: int
    concept: str
    category: str
    explanation: str
    examples: list[FocusExample]
    occurrences: int
    correct_streak: int
    mastery_streak: int
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    mastered_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("first_seen_at", "last_seen_at", "mastered_at")
    def serialize_datetime(self, v: datetime | None, _info):
        return v.isoformat() if v else None


class FocusAreaUpdate(BaseModel):
    status: Literal["active", "mastered", "dismissed"]


class DrillItemResponse(BaseModel):
    sentence: str
    answer: str
    accepted: list[str]
    hint: str
    translation: str


class DrillResponse(BaseModel):
    items: list[DrillItemResponse]


class PracticeResult(BaseModel):
    correct: int = Field(ge=0, le=20)
    total: int = Field(ge=1, le=20)


class FocusFlashcardsResponse(BaseModel):
    created: int
