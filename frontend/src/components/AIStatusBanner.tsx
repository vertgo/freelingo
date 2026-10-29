'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { apiFetch } from '@/lib/api'
import { AI_ERROR_EVENT, parseAIFailure, type AIFailure } from '@/lib/ai-errors'
import { useAuthStore } from '@/store/auth'

const DISMISSED_KEY = 'fl:ai-status-dismissed-at'

function readDismissedAt(): number {
  try {
    return Number(sessionStorage.getItem(DISMISSED_KEY)) || 0
  } catch {
    return 0
  }
}

function writeDismissedAt(at: number): void {
  try {
    sessionStorage.setItem(DISMISSED_KEY, String(at))
  } catch {
    // Storage unavailable: dismissal lasts until the next failure or reload.
  }
}

/**
 * Explains AI provider failures (quota, bad key, outage...) instead of letting
 * features fail silently. Shows failures reported by this page and the
 * server's most recent failure, which also covers background generation.
 */
export function AIStatusBanner() {
  const t = useTranslations('aiStatus')
  const pathname = usePathname()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const [failure, setFailure] = useState<AIFailure | null>(null)

  useEffect(() => {
    const show = (next: AIFailure) => {
      if (next.at <= readDismissedAt()) return
      setFailure((prev) =>
        prev && prev.at > next.at
          ? prev
          : { ...next, detail: next.detail ?? prev?.detail }
      )
    }
    const onEvent = (event: Event) => {
      const next = parseAIFailure((event as CustomEvent).detail)
      if (next) show(next)
    }
    window.addEventListener(AI_ERROR_EVENT, onEvent)

    let canceled = false
    apiFetch('/api/ai-status')
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (canceled) return
        const next = parseAIFailure(body?.failure)
        if (next) show(next)
        else if (body) setFailure(null)
      })
      .catch(() => {})

    return () => {
      canceled = true
      window.removeEventListener(AI_ERROR_EVENT, onEvent)
    }
  }, [pathname])

  if (!failure) return null

  const dismiss = () => {
    writeDismissedAt(failure.at)
    setFailure(null)
  }

  return (
    <div
      role="alert"
      className="border-fl-error/40 bg-fl-surface flex shrink-0 flex-wrap items-start gap-x-4 gap-y-1 border-b px-4 py-2"
    >
      <div className="min-w-0 flex-1 font-mono text-xs tracking-wide">
        <p className="text-fl-error-fg">
          ● {t('title', { service: t(`services.${failure.service}`) })}{' '}
          {t(`reasons.${failure.code}`)}
        </p>
        <p className="text-fl-muted-2 mt-0.5">
          {isAdmin ? t(`adminHints.${failure.code}`) : t('userHint')}
        </p>
        {isAdmin && failure.detail && (
          <p className="text-fl-muted-3 mt-0.5 break-words">
            {t('detailLabel')} {failure.detail}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={dismiss}
        className="text-fl-muted-1 font-mono text-xs underline transition-all hover:no-underline"
      >
        {t('dismiss')}
      </button>
    </div>
  )
}
