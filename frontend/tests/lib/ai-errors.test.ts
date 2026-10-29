import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  AI_ERROR_EVENT,
  aiErrorHeaders,
  reportAIErrorFromResponse,
  type AIFailure,
} from '@/lib/ai-errors'

function failed(headers: Record<string, string>) {
  return new Response('{}', { status: 503, headers })
}

describe('ai-errors', () => {
  const listener = vi.fn()
  window.addEventListener(AI_ERROR_EVENT, listener)

  afterEach(() => listener.mockReset())

  it('reports the reason carried by a failed response', () => {
    reportAIErrorFromResponse(
      failed({ 'X-AI-Error': 'quota', 'X-AI-Service': 'tts' })
    )
    expect(listener).toHaveBeenCalledOnce()
    const failure = (listener.mock.calls[0][0] as CustomEvent<AIFailure>).detail
    expect(failure).toMatchObject({ code: 'quota', service: 'tts' })
  })

  it('ignores responses without a reason, and successful ones', () => {
    reportAIErrorFromResponse(failed({}))
    reportAIErrorFromResponse(
      new Response('{}', { status: 200, headers: { 'X-AI-Error': 'quota' } })
    )
    expect(listener).not.toHaveBeenCalled()
  })

  it('copies only the AI error headers for proxies', () => {
    const res = failed({
      'X-AI-Error': 'auth',
      'X-AI-Service': 'llm',
      'Content-Type': 'application/json',
    })
    expect(aiErrorHeaders(res)).toEqual({
      'X-AI-Error': 'auth',
      'X-AI-Service': 'llm',
    })
  })
})
