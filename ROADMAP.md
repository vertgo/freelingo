# Roadmap

What's planned for this fork of FreeLingo. The goal is to learn **Mexican Spanish**:

- voice scenarios built around one grammar principle,
- targeted drills on the mistakes I actually make,
- a structured CEFR curriculum.

It borrows three ideas from the Cambridge English approach: high-frequency language first, anticipating predictable learner errors, and natural spoken language. An error log drives the drills.

Effort and payoff are estimates, not measurements. Items keep their IDs when they move between sections.

## Done

| Change | Branch |
| --- | --- |
| All-Gemini providers (LLM, TTS, STT, Live voice) and Mexican Spanish (`SPANISH_VARIANT=mx`) | `mexican-spanish-gemini` |
| Level picker, audio replay cache, building from source docs | `mexican-spanish-gemini` |
| Explain AI provider failures instead of failing silently | `ai-error-reporting` |
| Learners can use their own Gemini API key | `user-api-keys` |
| Focus areas: recurring conversation mistakes → drills, flashcards, tutor context | `focus-areas` |
| R3: curated common mistakes drive lesson exercises, traps and lesson-linked voice practice; Mexican overlay requires full Spanish spelling | `known-mistakes` |

## Now

These reuse data the app already has, so each is a small change.

| ID | Change | Effort | Payoff | Notes |
| --- | --- | --- | --- | --- |
| R4 | Pass the actual vocabulary word lists to lesson generation, not set IDs | Small | Medium | `generate_lesson` sends IDs like `saludos_a1` (`services/lesson_generator.py:166`), so the model guesses what a set contains. |
| R2 | Feed lesson exercise errors into focus areas | Small | High | Exercises already store corrections. Send only the wrong answers through the existing review/merge logic, so one-off misses don't create concepts. |
| R5 | Mexican Spanish touch-ups: a tú/usted rule and short spoken-style guidance (fillers, "¿mande?", "órale") | Small | Medium | The MX overlay (`services/prompts/common.py`) has the ustedes rule only. Speech-recognition locale isn't an issue: Gemini detects the language itself. Also fix docstrings that still say es-ES. |

## Next

Do these after using the Now items for a week or two.

| ID | Change | Effort | Payoff | Notes |
| --- | --- | --- | --- | --- |
| R6 | Voice entry point for the current unit's grammar point, not only finished lessons | Medium | High | This is the core goal. Lesson-linked voice already builds context from a completed lesson; extend it to the grammar point you're on now. |
| R1 | One grammar point per lesson slot, rotating through the unit | Small–medium | Medium | Do this alongside R6. See [R1 details](#r1-per-lesson-grammar-points). |

## Later

| ID | Change | Effort | Payoff | Notes |
| --- | --- | --- | --- | --- |
| R7 | Spaced re-checks for mastered focus areas (after a few days, then 1–2 weeks) | Small–medium | Medium | Three correct uses in a row isn't lasting mastery. Mastered areas never come back today. |
| R8 | Per-grammar-point competency scores derived from focus areas | Medium | Low–medium | All checklist items in a unit currently get the same average lesson score. |
| R9 | Seed focus areas from the placement test's per-question grammar slugs | Medium | Low–medium | Only matters once, at setup. |

## Backlog

These aren't planned. Pick one up only if it's needed.

| ID | Change | Notes |
| --- | --- | --- |
| R10 | Tag English-speaker-specific mistakes; fill thin C1/C2 coverage (7 mistakes each) | Content work that only pays off at advanced levels |
| R11 | Real corpus frequency ranks (e.g. `wordfreq`) | `frequency_rank` today is a curriculum order counter, not frequency data |
| R12 | CSV/YAML importer for outside curricula | A full CEFR curriculum already exists |
| R13 | Focus areas per study plan instead of per language | No effect with one learner and one plan |
| R14 | Lift usage limits for learners who bring their own API key | Offered during `user-api-keys` |
| R15 | "Require own key" mode for shared deployments | Offered during `user-api-keys` |

## R1: per-lesson grammar points

`distribute_units` (`backend/app/data/curriculum.py:297`) stores only the unit's first 2 grammar points and first vocabulary set on every lesson slot (`DayPlan.grammar_points` / `vocabulary_set_ids`). Nothing reads those fields:

- Lesson generation looks up the unit's full lists (`routers/study_plan.py:282-289`).
- The plan page and unit drawer show the unit's full lists from the curriculum data.

So nothing is skipped. The code is upstream (since 3d50a0f1) and cuts the list the same way in every course.

There are two ways to close this:

- **Preferred:** give each slot its own grammar point, rotating through the unit, and send it to lesson generation as the main focus, with the full list as background. That makes "one principle per lesson" explicit and gives R6 a clear grammar point to build on.
- **Otherwise:** delete the two dead fields. This could be a tidy upstream PR.

## Sources

- The audit of 2026-10-30, on branch `focus-areas` @ `2ca5986a`. A detailed copy with file paths is in `freelingo-audit-2026-10-30.md`, which is untracked.
- Follow-up check that the `distribute_units` truncation is unused.
