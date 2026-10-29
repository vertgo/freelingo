'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { AI_ERROR_CODES, type AIErrorCode } from '@/lib/ai-errors'

interface ApiKeyStatus {
  enabled: boolean
  has_key: boolean
  hint: string | null
  services: string[]
  warning?: string | null
}

type Message = { type: 'ok' | 'err'; text: string }

function asReason(value: unknown): AIErrorCode {
  return AI_ERROR_CODES.includes(value as AIErrorCode)
    ? (value as AIErrorCode)
    : 'unknown'
}

/**
 * Lets a learner use their own Gemini API key instead of the server's. The key
 * is checked and stored encrypted on the server; it never comes back to the
 * browser, only its last four characters.
 */
export function ApiKeySection() {
  const t = useTranslations('apiKey')
  const tServices = useTranslations('aiStatus.services')
  const [status, setStatus] = useState<ApiKeyStatus | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)

  useEffect(() => {
    let canceled = false
    apiFetch('/api/auth/me/api-key')
      .then((res) => (res.ok ? res.json() : null))
      .then((body: ApiKeyStatus | null) => {
        if (!canceled && body) setStatus(body)
      })
      .catch(() => {})
    return () => {
      canceled = true
    }
  }, [])

  if (!status?.enabled) return null

  async function save(event: React.FormEvent) {
    event.preventDefault()
    const apiKey = value.trim()
    if (!apiKey) return
    setBusy(true)
    setMessage(null)
    try {
      const res = await apiFetch('/api/auth/me/api-key', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey }),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) {
        const reason = asReason(body?.reason)
        setMessage({
          type: 'err',
          text:
            res.status === 422 ? t('invalidFormat') : t(`rejected.${reason}`),
        })
        return
      }
      setStatus(body)
      setValue('')
      setMessage({
        type: 'ok',
        text: body.warning
          ? t(`savedWarning.${asReason(body.warning)}`)
          : t('saved'),
      })
    } catch {
      setMessage({ type: 'err', text: t('rejected.unreachable') })
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setMessage(null)
    try {
      const res = await apiFetch('/api/auth/me/api-key', { method: 'DELETE' })
      if (!res.ok) throw new Error('remove failed')
      setStatus(await res.json())
      setMessage({ type: 'ok', text: t('removed') })
    } catch {
      setMessage({ type: 'err', text: t('removeFailed') })
    } finally {
      setBusy(false)
    }
  }

  const services = status.services
    .filter((s) => ['llm', 'tts', 'stt', 'voice'].includes(s))
    .map((s) => tServices(s))
    .join(' · ')

  return (
    <div className="border-fl-border bg-fl-surface border p-6">
      <div className="border-fl-border mb-5 flex items-center gap-2 border-b pb-4">
        <span className="text-fl-label text-fl-muted-2">●</span>
        <span className="text-fl-label text-fl-muted-2 font-mono tracking-widest uppercase">
          {t('title')}
        </span>
      </div>

      <div className="space-y-4">
        <p className="text-fl-muted-3 font-mono text-xs leading-relaxed">
          {t('description')}{' '}
          <a
            href="https://aistudio.google.com/apikey"
            target="_blank"
            rel="noreferrer"
            className="text-fl-fg underline hover:no-underline"
          >
            {t('getKey')}
          </a>
        </p>
        {services && (
          <p className="text-fl-hint text-fl-muted-4 font-mono">
            {t('usedFor', { services })}
          </p>
        )}

        <p className="text-fl-muted-2 font-mono text-xs tracking-wide">
          {status.has_key
            ? t('usingOwn', { hint: status.hint ?? '' })
            : t('usingServer')}
        </p>

        <form onSubmit={save} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="gemini-api-key" className="sr-only">
            {t('label')}
          </label>
          <input
            id="gemini-api-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={status.has_key ? t('placeholderReplace') : t('label')}
            className="bg-fl-bg border-fl-border text-fl-fg placeholder:text-fl-muted-4 focus:border-fl-border-2 min-w-0 flex-1 border px-4 py-3 font-mono text-sm transition-colors focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || !value.trim()}
            className="border-fl-accent bg-fl-accent text-fl-accent-fg border px-5 py-3 font-mono text-xs tracking-widest uppercase transition-opacity disabled:opacity-40"
          >
            {busy ? t('checking') : t('save')}
          </button>
        </form>

        {status.has_key && (
          <button
            type="button"
            onClick={remove}
            disabled={busy}
            className="text-fl-label text-fl-muted-2 border-fl-border hover:text-fl-error hover:border-fl-error/40 w-full border py-3 font-mono tracking-widest uppercase transition-colors disabled:opacity-40"
          >
            {t('remove')}
          </button>
        )}

        {message && (
          <p
            role="status"
            className={`border px-4 py-3 font-mono text-xs ${message.type === 'ok' ? 'border-fl-border text-fl-muted-1' : 'border-fl-error/40 text-fl-error'}`}
          >
            {message.type === 'ok' ? '✓ ' : '✕ '}
            {message.text}
          </p>
        )}

        <p className="text-fl-hint text-fl-muted-4 font-mono">{t('privacy')}</p>
      </div>
    </div>
  )
}
