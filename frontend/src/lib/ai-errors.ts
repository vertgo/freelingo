/**
 * AI provider failure reporting.
 *
 * The backend tags failed responses with `X-AI-Error` (quota, auth, overloaded,
 * timeout, unreachable, unknown) and `X-AI-Service` (llm, tts, stt, voice); SSE
 * and WebSocket errors carry the same code as `ai_error`. Anything that sees one
 * calls `reportAIError`, and the app-wide AIStatusBanner explains it.
 */

export const AI_ERROR_CODES = [
  'quota',
  'auth',
  'overloaded',
  'timeout',
  'unreachable',
  'unknown',
] as const
export type AIErrorCode = (typeof AI_ERROR_CODES)[number]

export const AI_SERVICES = ['llm', 'tts', 'stt', 'voice'] as const
export type AIService = (typeof AI_SERVICES)[number]

export interface AIFailure {
  code: AIErrorCode
  service: AIService
  /** Raw provider message; only sent to admins. */
  detail?: string
  /** Unix seconds. */
  at: number
}

export const AI_ERROR_EVENT = 'fl:ai-error'

const AI_ERROR_HEADER = 'X-AI-Error'
const AI_SERVICE_HEADER = 'X-AI-Service'

function asCode(value: unknown): AIErrorCode | null {
  return AI_ERROR_CODES.includes(value as AIErrorCode)
    ? (value as AIErrorCode)
    : null
}

function asService(value: unknown): AIService {
  return AI_SERVICES.includes(value as AIService) ? (value as AIService) : 'llm'
}

export function reportAIError(
  code: unknown,
  service: unknown,
  detail?: string
): void {
  const valid = asCode(code)
  if (!valid || typeof window === 'undefined') return
  const failure: AIFailure = {
    code: valid,
    service: asService(service),
    detail,
    at: Math.floor(Date.now() / 1000),
  }
  window.dispatchEvent(new CustomEvent(AI_ERROR_EVENT, { detail: failure }))
}

/** Report the failure carried by a backend response, if any. */
export function reportAIErrorFromResponse(res: Response): void {
  if (res.ok) return
  const code = res.headers.get(AI_ERROR_HEADER)
  if (code) reportAIError(code, res.headers.get(AI_SERVICE_HEADER))
}

/** Headers to copy when a route handler proxies a failed backend response. */
export function aiErrorHeaders(res: Response): Record<string, string> {
  const out: Record<string, string> = {}
  for (const name of [AI_ERROR_HEADER, AI_SERVICE_HEADER]) {
    const value = res.headers.get(name)
    if (value) out[name] = value
  }
  return out
}

export function parseAIFailure(value: unknown): AIFailure | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const code = asCode(raw.code)
  if (!code || typeof raw.at !== 'number') return null
  return {
    code,
    service: asService(raw.service),
    detail: typeof raw.detail === 'string' ? raw.detail : undefined,
    at: raw.at,
  }
}
