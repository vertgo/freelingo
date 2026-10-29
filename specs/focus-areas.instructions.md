---
description: "Current-state specification for focus areas: recurring learner mistakes found by reviewing conversations, tutor prompt injection, drills, flashcards, and mastery."
applyTo: "backend/app/models/focus_area.py, backend/app/schemas/focus.py, backend/app/services/focus_service.py, backend/app/services/prompts/focus.py, backend/app/routers/{focus_areas,chat,conversation}.py, backend/app/services/conversation_pipeline.py, frontend/src/app/(app)/focus/**, frontend/src/components/focus/**, frontend/src/lib/focus-areas.ts, messages/*.json"
---

# Focus Areas

## Purpose

A focus area is a mistake the learner keeps making in conversation with the tutor (any concept:
conjugation, mood, agreement, prepositions, word choice, false friends...). They are found
automatically, shown on `/focus`, fed back into tutor conversations, and practised with drills and
flashcards. They are scoped per user and target language.

## Finding them

`focus_service.review_conversation` sends the unreviewed part of a conversation (messages after
`conversations.focus_reviewed_through_id`, plus two earlier messages as context) to the LLM with
`REVIEW_SYSTEM_PROMPT` and gets `ConversationReview` (`mistakes`, `correct_uses`). The prompt includes
the language overlay (so e.g. Mexican usage is not flagged), the learner's existing focus areas (so
repeats reuse their id), and, for voice transcripts, a rule to ignore spelling and STT artifacts.

Reviews run in the background (the per-request API key context is inherited):

- **Voice:** when the conversation WebSocket closes, if the session has at least 2 new student messages.
- **Text chat:** after a reply, once 6 student messages are unreviewed (`CHAT_REVIEW_EVERY`).
- **Catch-up sweep:** starting a new chat or opening `GET /api/focus-areas` reviews the 3 most recent
  conversations that still have at least 2 unreviewed student messages.

## Merging and mastery

`apply_review` matches by `existing_id`, then by normalized concept label. A repeat increments
`occurrences` once per review, stores the example (most recent first, max 5), resets
`correct_streak`, and reactivates a mastered area. A dismissed area stays dismissed. Each id in
`correct_uses`, and each passed drill (≥ 80% correct), increments `correct_streak`; at 3
(`MASTERY_STREAK`) the area becomes `mastered`. Learners can also mark areas mastered, reopen, or
dismiss them.

## Tutor prompt

The top 3 active areas (by occurrences, then recency) are appended to the memory context for text
chat, the voice pipeline and Gemini Live (`build_focus_context`), escaped and marked as untrusted. The
tutor is told to create natural chances to use them, correct recurrences briefly, and never turn
the conversation into a drill.

## Practice

- `POST /api/focus-areas/{id}/drill` generates 5 fill-in-the-blank items (`DRILL_SYSTEM_PROMPT`). Cues
  never give away the choice being practised; choice concepts mix both options. Grading is
  client-side (`gradeAnswer`): case, spacing and surrounding punctuation are ignored, and a right
  answer missing only accents counts but is flagged.
- `POST /api/focus-areas/{id}/flashcards` generates 3 flashcards into the active plan with
  `source="focus"`, skipping words already in the plan.
