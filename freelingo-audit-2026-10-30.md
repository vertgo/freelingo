# FreeLingo audit (read-only)

Branch `focus-areas` @ `2ca5986a`, 2026-10-30. Nothing was changed during the audit. All paths are relative to the repo root.

---

## 1. Frequency-based vocabulary: **Partly** (the field exists, but the numbers aren't real frequency data)

- **Frequency data:** `VocabularyEntry.frequency_rank` exists in `backend/app/data/_types.py`. Spanish fills it for 1,036 of 1,181 entries in `backend/app/data/es/vocabulary_a1.py` through `vocabulary_c2.py`. The values aren't corpus ranks, though. They run as one counter across the whole dataset: A1 is 1–326, B2 is 594–761, C1 is 762–929, C2 is 930–1101. "hola" and "adiós" are both rank 1, and B1 goes alegría=456, tristeza=457, miedo=458. In practice it's a curriculum order number. The only place it's used is the `#N` badge in `frontend/src/app/(app)/vocabulary/[setId]/page.tsx:572`. The repo and DB contain no real frequency list, CSV or corpus.
- **Prompt limits:** The limits are by CEFR level only, never by frequency.
  - `LESSON_GENERATION_PROMPT` (`backend/app/services/prompts/lesson.py:150-175`) says "Do NOT use grammar or vocabulary above this level" and "Vocabulary must come from the vocabulary_set_ids listed or be common {cefr_level} words." **But `generate_lesson` only passes the set IDs** (`backend/app/services/lesson_generator.py:166`, e.g. `"saludos_a1"`), not the words in them. The LLM is guessing what those sets contain.
  - Flashcard prompts (`backend/app/services/prompts/flashcards.py`) and the tutor prompts take `{cefr_level}` and nothing else.
- **How flashcards get seeded:** Nothing is created automatically when a plan is made. Cards come from:
  1. "Add whole set" on a vocabulary page, which bulk-copies the curated words (`POST /api/flashcards/bulk`, `backend/app/routers/flashcards.py:205`).
  2. LLM generation from a topic plus CEFR level (`/generate`, `:304`).
  3. Tapping a word while reading (`/from-word`, `:379`, `WordTooltip.tsx`).
  4. Manual creation.
  5. A focus area's "make flashcards" button (`backend/app/routers/focus_areas.py:150`).

## 2. Known error patterns for English speakers: **Partly** (curated mistake lists exist but feed almost nothing)

- **Taxonomy:** Every `GrammarTopic` has `common_mistakes: [GrammarMistake(wrong, correct, note)]`. Spanish has 137 topics with 175 mistakes in total. Coverage drops off sharply at the top: A1 46, A2 48, B1 43, B2 24, C1 7, C2 7. One example is `ser`: "Estoy de Monterrey → Soy de Monterrey". The entries are generic learner errors, not ones specific to English speakers. There's no separate list or taxonomy of error types.
- **Where they're used:** They appear on the grammar reference page (`backend/app/routers/grammar.py:54,79`) and in the source JSON for the grammar "native help" prompt. **They are never passed to lesson generation, exercises or the tutor.**
- **Lesson prompts:** These ask the LLM to invent 2–4 `common_traps` in the learner's native language (`backend/app/services/prompts/lesson.py:231,269`). The `previous_lessons` block also tells it not to repeat traps already used. So the LLM writes the error anticipation fresh each time, and nothing designs exercises to draw a specific error out.
- **Tutor prompts:** These just say "when the student makes a grammar mistake, gently correct it." The one exception is the focus-area drill prompt (see 4), which forces choice-based items like ser/estar and por/para.

## 3. Error tracking: **Partly → Yes as of the last commit**

- **Lesson exercises:** `Exercise` (`backend/app/models/lesson.py:54-70`) stores `user_answer`, `score`, `feedback`, `corrections` (JSON) and `explanation` for each item. The detail is there, but it's never grouped by error type.
- **Skill scores:** `Progress.skills` (`backend/app/services/progress_service.py:117-125`) is a moving average (0.7 × old + 0.3 × new) **keyed by lesson type** (grammar, vocabulary, reading…), plus flashcard quality. It doesn't break down by grammar point.
- **Unit competencies:** `UserCompetency` rows (`backend/app/services/progress_service.py:153`, called from `backend/app/routers/lessons.py:372`) apply **the same average lesson score to every checklist item in the unit**. A unit with 5 competencies always shows identical scores across them, so they don't reflect specific errors at all.
- **Focus areas (new in commit `2ca5986a`):** `FocusArea` (`backend/app/models/focus_area.py`) is the only per-error-type store. An LLM reviews conversation transcripts and groups mistakes by concept (e.g. "Ser vs. estar"), with a category (grammar, vocabulary, word_order, other), up to 5 said→correction examples, an occurrence count and a correct-use streak. The prompt is `REVIEW_SYSTEM_PROMPT` in `backend/app/services/prompts/focus.py`, and the logic is in `backend/app/services/focus_service.py`. Reviews run after voice sessions and every 6 chat messages. **Lesson and game errors don't feed into it.**
- **Memories:** `memories` also hold free-text LLM notes, but nothing structured.

## 4. Targeted drills from errors: **Partly**

- **Focus-area drills:** `POST /focus-areas/{id}/drill` produces 5 fill-in-the-blank items with option cues ("___ (por / para)"). Scoring 80% or more counts toward mastery, and 3 correct uses in a row mark the area mastered (`backend/app/routers/focus_areas.py:111-148`). The top 3 active areas are also added to the tutor prompts for chat, the voice pipeline and Gemini Live (`FOCUS_CONTEXT_HEADER`).
- **Games:** Error Detective, sentence order and vocabulary pairs pull up to 16 recent mistakes from lesson exercises scored below 0.8 and from past game sessions, and use them to "inform priority" (`backend/app/services/games.py:197-257`).
- **Lesson-linked voice practice:** This sends the lowest-scoring answered exercises to the tutor (`backend/app/services/lesson_voice_practice.py`).
- **Review lessons:** Not driven by errors. `previous_lessons` is only there to avoid repetition.
- **SM-2:** Applies **only to flashcards** (`sm2_update` in `backend/app/services/flashcard_sm2.py`). Grammar points and focus areas aren't spaced. Mastery is a 3-in-a-row streak with no repeat schedule. The only indirect path is turning a focus area into flashcards, which then go through SM-2.

## 5. Voice conversation: **Both modes exist**

- **Topic link:** By default it's open-ended. Starting from a completed lesson passes `lesson_id`, and the backend then builds context from that lesson's title, objectives, explanation, vocabulary, grammar refs and up to 8 answered exercises (`backend/app/services/lesson_voice_practice.py`, `backend/app/routers/conversation.py:337-348`). The prompt then tells the tutor to guide the conversation around those objectives (`backend/app/services/prompts/tutor.py`, `lesson_practice_context`). It's tied to a finished lesson, not to the current unit as such.
- **Corrections:** These are deliberately sparse. The prompt only corrects when the mistake "causes misunderstanding, repeats, or is a key structure", and does it briefly at the end of the reply.
- **What gets saved:** The transcript (`Conversation` with `source="voice"` plus `ChatHistory`), memories through tool calls, usage and quota counts, and now focus-area extraction after the session.

## 6. Spoken Mexican Spanish: **Partly**

`SPANISH_VARIANT=mx` (`backend/app/core/config.py:39`, set in `.env:154`) changes three things:

- The prompt overlay (`_MEXICAN_SPANISH_OVERLAY`, `backend/app/services/prompts/common.py:99`): ustedes, Mexican vocabulary such as carro, celular and ahorita, a preference for the preterite, and Spain forms only as notes.
- The language label and flag (`backend/app/services/language_helpers.py:67`).
- TTS pronunciation guidance (`backend/app/services/prompts/speech.py:129`).

Beyond that:

- **Static data was already localized** in commit `cb6f714e`: "carro" appears 34 times against "coche" 4, "México" 51 times, and "vosotros" only in comparison notes. There's a C1 topic on Latin American Spanish (`backend/app/data/es/grammar_c1.py:522`) and a `registro-formal` topic at B2.
- **Register:** Phrasebook entries carry `register = formal | neutral | informal` (about 340 tagged in total).
- **Gaps:**
  - The MX overlay has **no tú/usted rule**, although the fr, de and it overlays all have one ("use tu/vous consistently…").
  - No prompt gives spoken-style guidance (fillers, contractions such as "pa'", colloquial "¿mande?", "órale").
  - Module docstrings still say "es-ES".

## 7. Curriculum: **Yes, and easy to extend**

- **Where it lives:** Python dataclasses in `backend/app/data/es/`:
  - `curriculum_{a1..c2}.py`: `CurriculumUnit` with id, title, grammar_points (slugs), vocabulary_set_ids, lesson_types, competency_checklist and prerequisite.
  - `grammar_*.py`, `vocabulary_*.py`, `phrasebook_*.py` and `assessment_bank.py`.
  - Assembly happens in `backend/app/data/es/curriculum.py`. `distribute_units` maps units onto week/day slots, which are stored as JSON in `StudyPlan.generated_plan`.
- **Lesson content:** Generated by the LLM on demand and stored in `Lesson.content` (JSON).
- **Adding outside content:** No DB migration needed. Write more dataclass entries and keep slugs and set IDs consistent with each other. The rules are in `specs/add-target-language.instructions.md` and enforced by `backend/tests/test_frontend_data_integrity.py`, `test_grammar.py` and `test_vocabulary.py`.
  - Without a script this is a hand-written Python step. A small importer from CSV or YAML to these dataclasses would be the cleanest route.
  - Lessons will only really use the new vocabulary once the prompt is sent the words themselves, not just set IDs (see 1).

---

## Summary

| # | Item | Status | Key point |
|---|---|---|---|
| 1 | Frequency vocabulary | Partly | `frequency_rank` is a sequential counter, not corpus data; prompts limit by CEFR only and receive set IDs, not word lists |
| 2 | Known learner errors | Partly | 175 curated `common_mistakes` (few at C1/C2), only on the grammar reference page; lesson traps are invented by the LLM |
| 3 | Error tracking | Partly → Yes | Exercises store answers and feedback; skills are per lesson type; competencies all get the same score; `FocusArea` is the only per-concept tracker (conversations only) |
| 4 | Drills from errors | Partly | Focus-area drills and flashcards, games weighted by past mistakes; SM-2 covers flashcards only |
| 5 | Voice conversation | Both | Open-ended by default; lesson-linked mode available; sparse corrections; transcript, memories and focus areas saved |
| 6 | Mexican Spanish | Partly | MX overlay, TTS guidance and localized data; no tú/usted rule; no spoken-style guidance |
| 7 | Curriculum | Yes | Python dataclasses under `backend/app/data/es/`; adding content needs no migration, and integrity tests check it |

## Other findings

- **Focus areas skip lesson exercises.** They're built only from conversations, even though `Exercise.corrections` and `feedback` already hold structured error data that could feed the same table.
- **Focus areas are stored per language, not per plan.** They're keyed by `(user_id, target_language)` instead of `study_plan_id`. Every other learning resource is owned by a plan.
- **Placement assessment** (`backend/app/services/assessment.py:42-81`) computes per-skill gaps (grammar, vocabulary, reading), and `AssessmentQuestion` has a `grammar_slug`. That's a possible starting point for diagnosing weak grammar points at the beginning.
- **Lesson-type rotation is fixed.** `distribute_units` gives each slot only the unit's first 2 grammar points and its first vocabulary set (`backend/app/data/es/curriculum.py:72-75`). Later grammar points in a unit can go untaught unless the LLM's own choice happens to cover them.
- **Data model mismatch for Mexican Spanish.** `SPANISH_VARIANT=mx` relabels `es-ES` rather than adding an `es-MX` code. STT and TTS locales follow that, so check that recognition really uses a Mexican locale if your provider supports one.
