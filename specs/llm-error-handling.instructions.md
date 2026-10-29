---
description: "Current LLM failure taxonomy, adapter retries, structured-output recovery, stream fallback, and feature-level error handling."
applyTo: "backend/app/services/llm_adapter.py, backend/app/services/**/*.py, backend/app/routers/**/*.py"
---

# LLM Error Handling

## Taxonomy

`llm_adapter.py` defines:

- `LLMError`: base normalized provider error.
- `LLMTimeoutError`: adapter timeout after retries.
- `LLMUnavailableError`: provider connection failure or rate limit after retries.
- `LLMResponseError`: empty, malformed, truncated, or schema-invalid output; may retain raw response.
- `LLMContextOverflowError`: declared context-specific subtype; current adapter does not raise it.
- `LLMToolsUnsupportedError`: explicit function/tool incompatibility.

Provider SDK exceptions are normalized where the adapter recognizes timeout, connection, rate-limit,
stream, or tool-capability failures. Rate limiting and provider unavailability share
`LLMUnavailableError`; there is no distinct provider-rate-limit HTTP contract.

## Adapter retries

The adapter performs up to three adapter-level invocations with increasing delays. `_call_with_retry`
currently retries any `LLMError`, not only transient subclasses. Anthropic disables SDK retries
explicitly; OpenAI Responses and the Chat Completions clients do not, so three adapter invocations
do not guarantee only three network requests.

The default request timeout is 120 seconds per network attempt, not an end-to-end generation limit.
OpenAI SDK `APITimeoutError` and Python `TimeoutError` normalize to `LLMTimeoutError`. Do not document
malformed output or context overflow as non-retryable while the default implementation catches the
common base class.

Listening and Reading call `structured_output` with an absolute monotonic deadline. This policy
disables adapter and SDK transport retries, sets each request timeout to the remaining budget, and
preserves timeout/provider exception types during JSON correction. It does not mutate shared client
configuration or change the default retry policy of other features. The outer background-job budget
also includes TTS and persistence and is configured by `EXERCISE_GENERATION_TIMEOUT_SECONDS`.
The exercise coordinator maps Python, HTTPX, normalized LLM, and OpenAI SDK timeout exceptions to the
same `timeout` status, including OpenAI TTS failures outside the LLM adapter.

## Structured output

`structured_output()` appends the JSON-only instruction, parses and validates the first response, and
performs one correction generation after parse/validation failure. If the correction path fails, its
broad exception handling wraps that failure as `LLMResponseError`, including a timeout or availability
error raised during the second generation under the default policy. With a deadline, typed LLM
errors are preserved and correction can only use the remaining budget.

Anthropic non-streaming responses stopped at `max_tokens` become `LLMResponseError` before downstream
JSON parsing and retain partial content for diagnostics.

OpenAI Responses must have status `completed` before their text is accepted. An `incomplete` response
raises `LLMResponseError`, including when its partial text happens to be valid JSON. Empty or
refusal-only output is not a successful generated resource. JSON prompting, Pydantic validation,
and the existing correction/deadline policy apply to OpenAI as to the other providers; this path does
not enable provider-side JSON Schema enforcement.

Only callers using `structured_output()` receive this correction behavior. Assessment free-write and
end-of-level test generation use raw `chat()` plus `json.loads()` and do not receive Pydantic recovery.

Error Detective, Sentence Order, and Vocabulary Pairs use the same deadline-aware structured-output
path for generation and semantic review. Up to two candidates share one inference budget; each
structured call retains the adapter's bounded JSON repair. Terminal state is stored in PostgreSQL.
Timeout, rejected content or provider failure prevents publication and releases the admission
reservation. Interrupted processes leave a generation that expires at its persisted deadline; late
results cannot overwrite terminal state.

## Context size

The adapter declares context constants and `LLMContextOverflowError` but does not currently estimate
tokens, trim messages, enforce model windows, or raise that subtype. Context windows vary by selected
model and must not be documented as fixed provider-wide values.

Callers apply bounded histories independently: text chat limits recent messages and voice keeps a
bounded in-memory context. A provider can still reject oversized prompts as a normalized generic
error.

## Streaming failures

Opening a stream and iterating a stream are different boundaries. Adapter-level retry protects stream
creation; failures after iteration begins do not generally restart through `_call_with_retry`.

OpenAI Responses streams require a `response.completed` terminal event. Explicit failed/error events
raise normalized LLM errors; incomplete or prematurely closed streams raise `LLMResponseError`.
`rate_limit_exceeded` response/event codes map to `LLMUnavailableError`. SDK timeouts and HTTPX2
transport timeouts map to `LLMTimeoutError`, and HTTPX2 network failures to `LLMUnavailableError`.
Streams close their HTTP response on completion, error, or cancellation. Terminal usage is captured
even on failed/incomplete responses when available; reasoning and function argument deltas are never
yielded as visible text. Tool execution waits for a completed response, not individual item events.

Tool-enabled streams have additional recovery:

- execute at most one native tool call;
- explicit incompatibility avoids repeating the same tool request;
- failure before visible output can retry the complete turn without tools;
- incompatibility or continuation failure after visible output emits a reset before no-tools retry;
- empty fallback output raises `LLMResponseError`;
- tool executor/persistence failure is returned to the model and does not confirm a memory save.

Voice remembers explicit incompatibility only for the current WebSocket session. A later session probes
again.

## Feature boundaries

There is no universal HTTP mapping for every LLM exception.

- Some synchronous Assessment and Flashcard endpoints distinguish timeout (504), unavailable (503),
  and invalid output (502).
- Native-resource and lesson-help endpoints commonly map all normalized LLM failures to 503.
- Dashboard-banner translation maps normalized LLM failures to 502.
- Exercise evaluation can return a deterministic unavailable/fallback result instead of an HTTP error.
- Listening and Reading generation runs in background; failure is logged and `/next` exposes
  `failed` status with `timeout`, `generation_failed`, or `interrupted`. Available persisted exercises
  take priority over operational status.
- Chat emits JSON SSE error events, never `[ERROR]` text markers.
- Voice emits structured WebSocket error frames and may keep recoverable sessions open.

`api-endpoints.instructions.md` and each domain spec own the observable status/event contract.

## Diagnostics

`LLMResponseError.raw_response` is available for diagnostics but is not logged systematically by every
caller. Logging must avoid credentials, private memories, full personal prompts, and unnecessary raw
learner content.

## Failure reasons shown to users

`app/services/ai_errors.py` reduces every LLM, TTS, STT, and Gemini Live failure to a reason code:
`quota` (429 / resource exhausted / rate limit), `auth` (rejected or invalid API key), `overloaded`
(5xx / high demand), `timeout`, `unreachable`, or `unknown`. It follows the exception's cause chain,
so normalized `LLMError`s keep the provider's reason. Codes are recorded where failures become final:
after adapter retries are exhausted, when a stream fails, on structured-output deadlines, in the
`synthesize`/`transcribe` methods of every TTS/STT service (`reports_ai_failure`), and when a Gemini
Live session fails.

- **HTTP:** `AIErrorMiddleware` adds `X-AI-Error` and `X-AI-Service` (`llm`, `tts`, `stt`, `voice`)
  to any 4xx/5xx response produced after a recorded failure. When an endpoint lets the provider
  exception escape, the middleware answers 503 with `{"detail", "ai_error"}` instead of a bare 500.
  CORS exposes both headers, and the frontend's TTS, STT, chat, and tour route handlers forward them.
- **Streams and sockets:** chat SSE error events and voice WebSocket `error` messages carry `ai_error`.
- **Last failure:** the most recent failure is stored in Redis (`ai:last_failure`, 7 days) and is
  cleared by the next success of the same service in that process. `GET /api/ai-status` returns it
  for 15 minutes (the raw provider message only to admins), and `GET /api/admin/health` includes it
  as `last_ai_failure`. This also surfaces failures from background generation.
- **Frontend:** `apiFetch` reports tagged responses, and `AIStatusBanner` in the app layout explains
  the failing feature and reason, with operator hints for admins and a retry hint for learners. It
  re-checks `/api/ai-status` on navigation, and dismissal lasts until a newer failure.

## Provider differences

Anthropic extracts system messages into its separate system parameter, requires a configured output
token budget, and uses provider-specific stream/content shapes. Ollama, DeepSeek, Codex, and Gemini
share the Chat Completions request path. Gemini merges system messages and adds a user turn when none
exists; inside each adapter invocation, a 429/503 or overload error moves the same request to the next
`GEMINI_FALLBACK_MODELS` entry before normal normalization and adapter retries apply. OpenAI uses
Responses with stateless native tool continuation and no model-name reasoning exceptions. These
differences remain internal to the adapter.

## Maintenance rules

- Document implemented behavior, not desired retry or context-management policy.
- Preserve normalized exceptions when changing catch order; avoid converting typed availability errors
  into response-format errors unintentionally.
- Treat failures before visible stream output differently from failures after partial output.
- Keep error messages provider-neutral unless operator action is genuinely provider-specific.
- Update endpoint/domain specs when an observable status or stream event changes.

## Learner API keys

Learners can save their own Gemini key in Settings → Account (`ApiKeySection`). `get_current_user`
and the voice WebSocket call `use_user_api_key`, which sets a per-request ContextVar; the Gemini
LLM client (`LLMAdapter._chat_client`), Gemini TTS/STT (`_client`), and Gemini Live
(`gemini_api_key()`) use it instead of `GEMINI_API_KEY`, with one cached SDK client per key. Tasks
started from the request inherit it. A failing learner key is reported to that learner (headers, SSE,
WebSocket) but never written to `ai:last_failure`, and there is no fallback to the server key.
`AIStatusBanner` uses `own_key_services` to tell the learner to check their own key for `auth` and
`quota` failures. Set `ALLOW_USER_API_KEYS=false` to turn the feature off.
