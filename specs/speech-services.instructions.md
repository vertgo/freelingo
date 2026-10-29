---
description: "Current-state specification for text-to-speech and speech-to-text providers, backend gateways, plan-derived recognition language, persistent audio, and reusable frontend audio components."
applyTo: "backend/app/services/{tts_service,stt_service,chat_markdown,gemini_live_pipeline}.py, backend/app/routers/{tts,stt}.py, backend/app/schemas/tts_stt.py, backend/app/core/config.py, backend/app/main.py, frontend/src/components/ui/{AudioPlayer,VoiceRecorder,exercise-audio-player}.tsx, frontend/src/app/api/{tts,stt}/**, docker-compose*.yml, .env.example"
---

# Speech Services

## Purpose

The backend is the only gateway to speech providers. Browser code never calls Kokoro, Whisper,
OpenAI, or Gemini speech APIs directly and never receives provider credentials.

Text-to-speech (TTS) and speech-to-text (STT) are configured independently. Their backend service
objects are created during FastAPI startup and stored in `app.state.tts_service` and
`app.state.stt_service`.

## Provider configuration

TTS configuration:

- `TTS_PROVIDER=local` selects Kokoro; `openai` selects OpenAI TTS; `gemini` selects Gemini TTS.
- `TTS_BASE_URL` defaults to `http://kokoro:8880`.
- `TTS_VOICE` defaults to `af_heart` for Kokoro.
- `OPENAI_TTS_MODEL` defaults to `tts-1` in backend `Settings`. Both Compose files instead supply
  `gpt-4o-mini-tts` when the operator omits the variable; this model is used when `TTS_PROVIDER=openai`.
- `OPENAI_TTS_VOICE` defaults to `nova`.
- `OPENAI_TTS_SPEED` defaults to `1.0`.
- `GEMINI_TTS_MODEL` defaults to `gemini-3.8-flash-tts` and `GEMINI_TTS_VOICE` to `Kore`.
- `GEMINI_TTS_FALLBACK_MODELS` defaults to `gemini-3.8-flash-lite-tts` and
  `gemini-3.1-flash-tts-preview`.

STT configuration:

- `STT_PROVIDER=local` selects the local Whisper service; `openai` selects OpenAI transcription;
  `gemini` selects Gemini transcription.
- `STT_BASE_URL` defaults to `http://whisper:9000`.
- `OPENAI_STT_MODEL` defaults to `whisper-1`.
- `GEMINI_STT_MODEL` defaults to `gemini-3.5-transcribe`.
- `STT_MODEL` and `STT_ENGINE` configure the local Whisper container as `ASR_MODEL` and
  `ASR_ENGINE`; they are not backend `Settings` fields.

`OPENAI_API_KEY` is required at startup when either selected provider is OpenAI, and `GEMINI_API_KEY`
when either is Gemini. Provider values are not enum-validated: values other than `openai` and
`gemini` currently select the local adapter.

`VOICE_PROVIDER` selects the voice-conversation engine independently: `pipeline` (default) uses the
configured STT, LLM, and TTS; `gemini_live` uses one Gemini Live session (see below) with
`GEMINI_LIVE_MODEL` (default `gemini-3.8-live`) and `GEMINI_LIVE_VOICE` (default `Kore`).

The public configuration exposes the selected TTS provider and default OpenAI TTS voice. It does
not expose provider credentials or the selected STT provider.

## TTS adapters

### Kokoro

`KokoroTTSService`:

- checks health with `GET {base_url}/v1/models` and a five-second timeout;
- synthesizes with `POST {base_url}/v1/audio/speech` and a 30-second timeout;
- sends model `kokoro`, text, voice, and MP3 response format;
- uses the requested voice, otherwise a per-language default voice (`bf_emma` for `en-GB`, `ef_dora`
  for Spanish, `ff_siwis` for French, `if_sara` for Italian, `pf_dora` for Portuguese, `jf_alpha` for
  Japanese, `zf_xiaobei` for Chinese), otherwise configured `TTS_VOICE`;
- raises on non-success HTTP responses;
- returns response bytes without validating that they contain a non-empty MP3;
- uses the language argument only for that voice choice; Kokoro selects pronunciation from the voice
  prefix and has no German or Korean voice.

### OpenAI

`OpenAITTSService`:

- checks health by listing models;
- sends configured model, requested or default voice, text, MP3 format, and speed;
- trims input and returns empty bytes for empty trimmed text;
- rejects an empty provider audio response;
- records request metadata and latency without logging credentials;
- sends pronunciation `instructions` only for `gpt-4o-mini-tts` and its snapshot identifiers;
- omits `instructions` for `tts-1`, `tts-1-hd`, and unrecognized models, preserving compatibility.

The model remains selected by `OPENAI_TTS_MODEL`; using the `gpt-4o-mini-tts` alias does not pin a
snapshot. `prompts/speech.py` owns instructions shared by all OpenAI synthesis callers. They request
faithful reading, native pronunciation and natural prosody for each language in the text, including
language switches. Text is spoken content, not instructions to execute. Generic calls without a
language ask the model to infer it from the text; isolated ambiguous words cannot reliably identify
a language or regional variety.

Known language arguments add explicit regional guidance for supported learned languages and UI
locales: `es`/`es-ES` requests Spain pronunciation (Mexico City Mexican Spanish with seseo when
`SPANISH_VARIANT=mx`), `pt`/`pt-PT` requests Portugal pronunciation, `en`/`en-GB` requests British
pronunciation, and `en-US` requests American pronunciation. Without a language argument, instructions
do not impose a regional accent. Conversation, Listening, and Phrasebook supply their full
target-language code; the tour supplies its UI locale. Legacy models ignore this language argument and
Kokoro uses it only for its default voice. Model instructions do not alter the input text or select a
different voice.

Each supported language has focused pronunciation guidance for vowel and consonant quality, rhythm,
stress, and relevant tonal/pitch distinctions. It asks for everyday native articulation without
exaggeration, applying those details only to passages in the indicated language. The guidance applies
to any selected voice, preserving its vocal identity. `nova` is the configured default, not a forced
voice; user preferences continue to take precedence. These instructions guide the model rather than
guaranteeing accent quality for every voice or text.

Only Kokoro chooses a voice from the target language; no adapter chooses a model from it.

### Gemini

`GeminiTTSService`:

- checks health by fetching the configured model;
- trims input and returns empty bytes for empty trimmed text;
- uses the requested voice only when it is a supported Gemini voice (`Kore`, `Aoede`, `Leda`,
  `Zephyr`, `Puck`, `Charon`, `Fenrir`, `Orus`), otherwise `GEMINI_TTS_VOICE`;
- always prefixes the text with the shared `prompts/speech.py` instructions for the supplied language;
- requests audio output with a 60-second timeout;
- on HTTP 429 or 503 retries the request on each `GEMINI_TTS_FALLBACK_MODELS` entry in order, because
  quotas are per model; other errors, or failure of the last model, propagate;
- rejects an empty audio payload, wraps raw PCM16 (24 kHz unless the MIME type states a rate) in a WAV
  header, and encodes the WAV to 64 kbit/s MP3 with `ffmpeg`, failing if `ffmpeg` fails.

Its cache keys follow the OpenAI instruction-capable pattern and are always used: they hash text,
language context, provider, primary model, voice, MP3 format, and instructions. A fallback model's
output is cached under the primary model's key.

## STT adapters

Every STT adapter requires an explicit keyword-only ISO 639-1 language on every transcription call.
There is no implicit English fallback.

`WhisperSTTService`:

- checks health with `GET {base_url}/`, following redirects, and a five-second timeout;
- sends `POST {base_url}/asr` with `output=json`, explicit `language`, and `task=transcribe`;
- uploads the file under multipart field `audio_file`;
- uses a 60-second timeout;
- extracts and trims the JSON `text` field.

`OpenAISTTService`:

- checks health by listing models;
- sends configured model, file bytes, filename, MIME type, explicit language, and a 60-second
  timeout to OpenAI transcription;
- extracts and trims the returned text.

`GeminiSTTService`:

- checks health by fetching the configured model;
- sends the audio bytes with their MIME type to `GEMINI_STT_MODEL` with a 60-second timeout;
- does not send the language: the transcription model detects it and rejects developer instructions,
  so the required argument is only logged;
- joins the trimmed transcription or text parts of the response.

## HTTP API

### `POST /api/tts`

- Requires authentication.
- Rate limit: `20/minute`.
- Accepts JSON text of 1-5000 characters and an optional voice string.
- Accepts an optional `study_plan_id` or `conversation_id`, but not both. IDs must be strict positive
  PostgreSQL-range integers; invalid or conflicting context returns `422`.
- Verifies that a supplied plan/conversation belongs to the authenticated user; missing or foreign
  resources return `404`. A plan supplies its persisted BCP-47 language. A conversation uses its
  owned plan's language when present, otherwise its stored target language. Active-language selection
  is never used to resolve supplied context. Without context, synthesis infers language from the text.
- Ignores the client voice when the configured provider is local, preventing stale OpenAI voice
  preferences from reaching Kokoro. Gemini receives it but replaces unsupported names with its
  configured voice.
- With `conversation_id`, converts the supplied Markdown to spoken text through
  `chat_markdown_to_speech()` after ownership and language resolution. Bold/italic markers and list
  numbering/bullets are omitted; words, literal punctuation and paragraph/list boundaries are kept.
  Bare list items receive a sentence boundary, using a full-width stop for Japanese and Chinese.
  Formatting carries no guaranteed vocal emphasis. Content without speakable text returns `422`
  without calling the provider. This also applies to replaying voice-origin history from the chat page.
- Plan-only and context-free requests pass their text directly to the provider. The WebSocket voice
  pipeline does not use this conversion. Stored transcripts retain their original content.
- Returns `audio/mpeg` bytes.
- Accepts or creates `X-TTS-Trace-ID` and returns backend synthesis and total latency headers.
- Returns `503` only when no TTS service object is registered; provider exceptions otherwise
  propagate through normal server error handling.

### `GET /api/tts/preview/{voice}`

- Requires authentication.
- Rate limit: `60/minute`.
- Exists only for OpenAI TTS; local and Gemini requests return `404`.
- Accepts `alloy`, `ash`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, or `shimmer`.
- Invalid voices return `400`; a missing service object returns `503`.
- Generates the Lingu preview and atomically caches it under `/app/tts_previews/`.
  Instruction-capable OpenAI models use `{voice}-{sha256}.mp3`, with text, model, voice, speed, format,
  language context, and instructions in the identity; other models retain `{voice}.mp3`.
- Returns `Cache-Control: no-store`; Settings requests previews with `cache: 'no-store'` so browser
  caches cannot mask synthesis changes behind the unchanged public URL. The backend disk cache is
  retained.

### `POST /api/tts/tour/{locale}/{step}`

- Requires authentication; rate limit `20/minute`.
- Accepts one of the fifteen UI locales and `step1` through `step7`; unknown values return `404`.
- `messages/*.json` owns tour copy. The frontend resolves the displayed paragraph through i18n and
  sends it as `text` in the JSON request body. The backend validates text length (1–5000 characters;
  invalid or missing text returns `422`) and synthesizes that supplied text.
- An optional JSON `voice` selects a supported OpenAI voice (`400` for an invalid selection).
  Omission uses the configured default. Kokoro always uses its configured default, ignoring client voice preferences.
  Gemini voices are not validated here; unsupported names fall back to `GEMINI_TTS_VOICE` in
  synthesis.
  Locale follows the interface, not the learned language, and supplies pronunciation guidance when
  the configured OpenAI model supports instructions.
- `services/tour_audio.py` stores shared MP3 files at
  `{AUDIO_STORAGE_PATH}/tour/{locale}/{sha256}.mp3`. The hash includes the exact text, locale,
  effective provider/model/voice/speed, output format, and instructions when supported. Gemini uses its
  own cache key, which has no speed. Requests never overwrite a different version.
- Shared-volume file locks serialize generation across workers; cancellable acquisition and synthesis
  share a 60-second deadline. A second request rechecks the file after obtaining the lock. Writes use
  unique temporary files and atomic replacement; empty output and failures do not publish audio.
- Returns `audio/mpeg` and `Cache-Control: no-store`: the backend disk cache is authoritative because
  the URL does not identify text or provider revisions. Missing service/provider failure returns `503`;
  the generation deadline returns `504`. The UI permits retry without blocking the tour.
- The Next.js proxy has a 70-second deadline and the browser a 75-second budget. Browser navigation
  cancels its request and discards late responses; already-started backend generation may finish and
  populate the shared cache after the browser leaves.

### `POST /api/stt`

- Requires authentication.
- Rate limit: `20/minute`.
- Accepts multipart `audio` and a required positive PostgreSQL-range `study_plan_id`.
- Verifies the plan belongs to the authenticated user.
- Derives the BCP-47 target language from that persisted plan and converts it to ISO 639-1.
- Preserves the uploaded filename and MIME type, with WebM defaults when absent.
- Reads the upload into memory and rejects payloads larger than 50 MiB with `413`.
- Returns `404` for an absent or foreign plan, `422` for invalid multipart data, and `503` when no
  STT service object exists.
- Returns `{ "text": string }`, including an empty string if the provider produces one.

The endpoint does not currently validate accepted MIME types, extensions, non-empty audio, or audio
integrity.

## Resource-owned recognition

Pronunciation exercises and flashcard speaking mode capture the owning `study_plan_id` when recording
starts. A later component update or active-language switch does not alter the in-flight upload.

Lesson pronunciation evaluation compares the transcription with the persisted target sentence using
the plan's target language, the user's native language, and the lesson level. This evaluates
transcribed text, not acoustic phonemes. If LLM evaluation fails, normalized equality or containment
provides the deterministic fallback.

Flashcard speaking comparison occurs in the frontend after STT. The UI shows the transcription,
expected word and expected-word audio before the learner saves the result. Normalized exact equality
proposes SM-2 quality 5; other results propose quality 2. The learner may retry before confirming or
correct a recognition mismatch by confirming quality 5. Only confirmation sends a review request;
recordings and retries do not award XP or alter scheduling. The latest transcription determines the
proposed quality. Empty normalized transcription keeps the recorder available with a retry message.
This comparison does not evaluate acoustic pronunciation. A failed save retains the result, and
concurrent confirmation is blocked until the request settles.

Before feedback, examples replace all exact target-term matches with blanks, ignoring case and
normalizing Unicode composition and inter-word whitespace. Latin terms use Unicode letter/number
boundaries; CJK terms can match without spaces. Examples without a maskable term, including inflected
forms that do not match the stored word, are withheld until feedback. Word, definition, translation
or example edits discard pending speech feedback. Mode, card, account and language changes unmount
the previous speech interaction and discard delayed transcription callbacks.

Voice conversation uses its own capture pipeline and WebSocket contract, described in
`voice-conversation.instructions.md`.

## Frontend components and proxies

`AudioPlayer` requests TTS, creates a Blob URL, and plays it with the browser Audio API. Voice
precedence is explicit prop, stored `tts_voice`, then backend default. It supports loading, playing,
stop, and error states and is used across lessons, flashcards, vocabulary, chat, and phrasebook.
Error Detective also uses this same component and endpoint for the full corrected sentence, after
the correction step has been submitted. Generated explanations are not spoken. Existing provider
and voice-preference rules apply; audio failure does not change the game result or block completion.

`AudioPlayer` defaults to POST `/api/tts`; a custom `audioUrl` defaults to GET. Setting `audioMethod`
to POST sends the text, resolved voice, and optional context as JSON to that URL. Lessons, flashcards,
saved vocabulary, and games pass their resource's `studyPlanId`; chat passes `conversationId`.
Changing either context cancels pending playback and releases the previous audio. Custom GET requests
use `cache: 'no-store'`, bypassing previously cached browser audio while retaining backend disk caches.
Both GET and POST audio use the shared authenticated `apiFetch` path. A 401 can renew the access
token and retry the same audio request once, preserving body, resource context, trace headers and
binary response handling. The player's timeout includes token renewal. Canceling an audio consumer
cancels its wait/retry without canceling renewal shared by other requests. Changing account sessions
cancels playback and discards late audio; token rotation within the same session does not interrupt it.

The player keeps its fetched Blob URL after playback ends or is stopped, so replaying the same
content plays from memory without another TTS request. Errors, unmount, account-session changes, and
changes of text, URL, method, resolved voice, or resource context release it. Only one player sounds
at a time: starting one stops the previously active player and aborts its pending request.

The dashboard tour posts its displayed i18n text and voice to its locale/step audio URL with this
player. Optional playback-state notifications coordinate Lingu's speaking animation; they do not
provide phoneme-level lip sync.
Stopping, replacing content, and unmounting abort requests, stop playback, remove audio handlers, and
release Blob URLs and timers. Each tour screen requires its own manual playback action.

`VoiceRecorder`:

- requires a resource-owning plan ID;
- obtains mono microphone audio with echo cancellation, noise suppression, and automatic gain;
- captures PCM with Web Audio, resamples to 16 kHz, and encodes WAV PCM16;
- stops manually or at its configured maximum duration;
- uploads WAV plus the captured plan ID to `/api/stt`;
- awaits synchronous or asynchronous result handling before returning idle;
- stops late permission streams and aborts pending STT on unmount;
- prevents another recording while transcription or result handling is pending.

The dedicated Next.js TTS route forwards authentication and trace context but buffers the backend
audio before responding. The STT route parses and reconstructs multipart data, forwards auth and
cookies, and propagates request cancellation to the backend.

## Persistent and transient audio

`POST /api/tts`, STT recordings, and voice-conversation audio are transient.

Persistent MP3 uses include:

- Listening: `{AUDIO_STORAGE_PATH}/listening/{exercise_id}.mp3`.
- Phrasebook: hashed files below `{AUDIO_STORAGE_PATH}/phrasebook/{iso}/`.
- OpenAI previews: `/app/tts_previews/{voice}.mp3` or `{voice}-{sha256}.mp3`, depending on model support
  for pronunciation instructions.
- Dashboard tour: `{AUDIO_STORAGE_PATH}/tour/{locale}/{sha256}.mp3`.

For instruction-capable OpenAI models and Gemini, Phrasebook uses the adapter's synthesis hash
covering text, language, model, voice, format, instructions, and (OpenAI only) speed. Other
providers/models retain the category/phrase/text cache identity. Phrasebook responses use
`Cache-Control: no-store` because their public URL does not identify the synthesis settings. Tour,
Phrasebook, and voice-preview audio is generated on demand for a new key, leaving previous files
intact. Listening recordings remain attached to their exercises and are not regenerated by model or
prompt changes. A provider-side update behind a model alias does not change cache keys.

The compose stack mounts persistent host storage for generated audio and previews. Local Kokoro and
Whisper services use internal network addresses and are not called from the frontend.

## Gemini Live voice

With `VOICE_PROVIDER=gemini_live`, the conversation router runs `GeminiLivePipeline` instead of the
STT-LLM-TTS pipeline. It opens one Gemini Live speech-to-speech session per WebSocket conversation and
needs no TTS or STT service object. The browser protocol is unchanged: frontend VAD sends one WAV per
utterance and receives status, transcript, binary audio, and the existing turn events. Voice
preferences are kept only when they name a supported Gemini voice (also under `TTS_PROVIDER=gemini`);
otherwise `GEMINI_LIVE_VOICE` applies.

Session setup:

- The system instruction is the normal conversation system prompt, built at connection with the
  memory context, plus a spoken-call addendum (no lists or formatting, pace suited to the CEFR level).
- Audio output with input and output transcription; Gemini automatic activity detection is disabled.
- On first connect, the pipeline's initial context messages (capped at 20) are sent as prior turns,
  followed by a greeting request. The greeting persists only the assistant message.

Turns:

- Each WAV is converted to 16 kHz mono PCM16 (downmixed and resampled when needed), then sent as one
  explicit activity: activity start, 100 ms audio chunks, activity end. This keeps Gemini's turns
  aligned with the frontend's. Unreadable or empty WAVs are ignored.
- The backend emits `transcribing` on receipt and `thinking` after sending. Gemini's input
  transcription replaces STT; the final user transcript is sent before the first reply audio.
- Reply PCM (24 kHz) is wrapped as WAV chunks: the first after 250 ms of audio, then about one second
  each. After audio starts, non-final assistant transcripts stream from output transcription; the
  final transcript, `listening`, and `turn_complete` follow at generation/turn completion.
- Completed turns with user text persist user and assistant messages through the normal pipeline
  path, including rewards. Token usage reported by the session is stored with
  `source="conversation"`.
- A turn with no reply output after 20 seconds (for example, noise only) is released with `listening`
  and `turn_complete` and is not persisted.

Barge-in and interruption: a new utterance marks any reply that has started output as stale, drops
its remaining audio and transcript without persisting it, and emits `barge_in` if one was still
unfinished. User text of activities Gemini has not answered is carried into the next turn, because
Gemini merges them. An explicit `interrupt` drops in-flight replies the same way and replies
`interrupted`. If Gemini reports an interruption before the backend marked the reply stale, that
reply is discarded and the turn stays open for the real answer.

Memory: when memory tools are available for an authenticated user, the session declares
`save_user_memory` as a non-blocking function. Calls are executed through the shared memory service
with voice provenance, answered with silent function responses, and a confirmed save emits
`memory_updated`. Memory context is not refreshed per turn within one connection.

Session length: sliding-window context compression lifts the Live audio session length cap, and
session resumption handles are tracked continuously. After a `GoAway` (once no reply is producing
output) or a server-closed connection, the session reconnects with the latest handle; unfinished turns
from the old connection are closed in the UI without persistence, and the greeting is not repeated. If
the connection ends without a resumption handle, or the session fails, the backend sends
`internal_error` and closes with 1011. Maximum-duration and inactivity watchers behave as in the
pipeline.

## Availability semantics

Startup creates configured adapter objects but does not prove provider health. Administrative health
checks call adapter `health()` methods. Conversation warmup attempts provider work in parallel within
a shared 60-second budget. It logs and suppresses individual failures, cancels unfinished probes at
the deadline, and logs the timeout. Its `ready` response is therefore not a strict health guarantee.
The conversation frontend allows 75 seconds for the warmup request, including transport and
authentication overhead, and aborts its request on timeout, manual stop, or unmount.
With `VOICE_PROVIDER=gemini_live`, warmup returns `ready` after its access checks without provider
probes, because the Live session connects per conversation.

## Related specifications

- `voice-conversation.instructions.md` — WebSocket conversation pipeline.
- `listening.instructions.md` — persistent TTS audio for Listening exercises.
- `multi-language.instructions.md` — plan ownership and target-language rules.
- `services.instructions.md` — complete backend service inventory.
- `docker.instructions.md` — deployment topology and provider containers.
