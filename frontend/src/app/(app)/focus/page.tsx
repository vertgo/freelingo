'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useLocale, useTranslations } from 'next-intl'
import { PageLoading } from '@/components/ui/page-loading'
import { FocusDrill } from '@/components/focus/FocusDrill'
import {
  createFocusFlashcards,
  fetchFocusAreas,
  parseServerDate,
  setFocusAreaStatus,
  type FocusArea,
} from '@/lib/focus-areas'

type Message = { type: 'ok' | 'err'; text: string }

const actionClass =
  'text-fl-muted-2 border-fl-border hover:text-fl-fg hover:border-fl-border-2 border px-3 py-2 font-mono text-xs tracking-widest uppercase transition-colors disabled:opacity-40'

function FocusAreaCard({
  area,
  onChange,
}: {
  area: FocusArea
  onChange: (updated: FocusArea | null) => void
}) {
  const t = useTranslations('focus')
  const locale = useLocale()
  const [drilling, setDrilling] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<Message | null>(null)
  const mastered = area.status === 'mastered'
  const lastSeen = parseServerDate(area.last_seen_at).toLocaleDateString(
    locale,
    { day: 'numeric', month: 'short' }
  )

  async function makeFlashcards() {
    setBusy(true)
    setMessage(null)
    try {
      const created = await createFocusFlashcards(area.id)
      setMessage({
        type: 'ok',
        text: created
          ? t('flashcardsCreated', { count: created })
          : t('flashcardsNone'),
      })
    } catch {
      setMessage({ type: 'err', text: t('flashcardsError') })
    } finally {
      setBusy(false)
    }
  }

  async function setStatus(status: FocusArea['status']) {
    setBusy(true)
    setMessage(null)
    try {
      const updated = await setFocusAreaStatus(area.id, status)
      onChange(status === 'dismissed' ? null : updated)
    } catch {
      setMessage({ type: 'err', text: t('updateError') })
      setBusy(false)
    }
  }

  return (
    <article className="border-fl-border bg-fl-surface border p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-fl-fg font-mono text-sm font-semibold">
          {area.concept}
        </h3>
        <span className="text-fl-muted-3 font-mono text-xs">
          {mastered
            ? t('masteredLabel')
            : t('seen', { count: area.occurrences, date: lastSeen })}
        </span>
      </div>
      {area.explanation && (
        <p className="text-fl-muted-2 mt-2 font-mono text-xs leading-relaxed">
          {area.explanation}
        </p>
      )}
      {area.examples.length > 0 && (
        <ul className="mt-3 space-y-1 font-mono text-xs">
          {area.examples.slice(0, 3).map((example) => (
            <li key={example.said} className="flex flex-wrap gap-x-2">
              <span className="text-fl-muted-3 line-through">
                {example.said}
              </span>
              <span aria-hidden="true" className="text-fl-muted-3">
                →
              </span>
              <span className="text-fl-fg">{example.correction}</span>
            </li>
          ))}
        </ul>
      )}
      {!mastered && (
        <p className="text-fl-muted-3 mt-3 font-mono text-xs">
          {t('streak', {
            count: area.correct_streak,
            total: area.mastery_streak,
          })}
        </p>
      )}

      {drilling ? (
        <FocusDrill
          area={area}
          onFinished={(updated) => onChange(updated)}
          onClose={() => setDrilling(false)}
        />
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setDrilling(true)}
            disabled={busy}
            className="border-fl-accent bg-fl-accent text-fl-accent-fg border px-3 py-2 font-mono text-xs tracking-widest uppercase disabled:opacity-40"
          >
            {t('practice')}
          </button>
          <button
            type="button"
            onClick={makeFlashcards}
            disabled={busy}
            className={actionClass}
          >
            {t('makeFlashcards')}
          </button>
          {mastered ? (
            <button
              type="button"
              onClick={() => setStatus('active')}
              disabled={busy}
              className={actionClass}
            >
              {t('reopen')}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setStatus('mastered')}
              disabled={busy}
              className={actionClass}
            >
              {t('markMastered')}
            </button>
          )}
          <button
            type="button"
            onClick={() => setStatus('dismissed')}
            disabled={busy}
            className={actionClass}
          >
            {t('dismiss')}
          </button>
        </div>
      )}
      {message && (
        <p
          role="status"
          className={`mt-3 font-mono text-xs ${message.type === 'ok' ? 'text-fl-muted-1' : 'text-fl-error'}`}
        >
          {message.type === 'ok' ? '✓ ' : '✕ '}
          {message.text}
          {message.type === 'ok' && (
            <>
              {' '}
              <Link href="/flashcards" className="underline">
                {t('openFlashcards')}
              </Link>
            </>
          )}
        </p>
      )}
    </article>
  )
}

export default function FocusPage() {
  const t = useTranslations('focus')
  const [areas, setAreas] = useState<FocusArea[] | null>(null)
  const [loadError, setLoadError] = useState(false)

  const load = useCallback(async () => {
    setLoadError(false)
    try {
      setAreas(await fetchFocusAreas())
    } catch {
      setLoadError(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function replace(id: number, updated: FocusArea | null) {
    setAreas((previous) =>
      (previous ?? []).flatMap((area) =>
        area.id !== id ? [area] : updated ? [updated] : []
      )
    )
  }

  if (loadError) {
    return (
      <div className="mx-auto max-w-4xl p-6 font-mono text-sm">
        <p className="text-fl-error">✕ {t('loadError')}</p>
        <button
          type="button"
          onClick={() => void load()}
          className="text-fl-muted-1 mt-2 underline"
        >
          {t('retry')}
        </button>
      </div>
    )
  }
  if (!areas) return <PageLoading />

  const active = areas.filter((a) => a.status === 'active')
  const mastered = areas.filter((a) => a.status === 'mastered')

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <header>
        <div className="mb-2 flex items-center gap-2">
          <span className="text-fl-label text-fl-muted-2">●</span>
          <span className="text-fl-muted-1 font-mono text-xs tracking-widest uppercase">
            {t('eyebrow')}
          </span>
        </div>
        <h1 className="text-fl-fg font-mono text-2xl font-semibold tracking-tight">
          {t('title')}
        </h1>
        <p className="text-fl-muted-3 mt-2 max-w-2xl font-mono text-sm leading-relaxed">
          {t('description')}
        </p>
      </header>

      {active.length === 0 ? (
        <div className="border-fl-border bg-fl-surface border p-6">
          <p className="text-fl-muted-2 font-mono text-sm leading-relaxed">
            {mastered.length ? t('allMastered') : t('empty')}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/chat" className={actionClass}>
              {t('goChat')}
            </Link>
            <Link href="/conversation" className={actionClass}>
              {t('goVoice')}
            </Link>
          </div>
        </div>
      ) : (
        <section className="space-y-3" aria-label={t('activeLabel')}>
          {active.map((area) => (
            <FocusAreaCard
              key={area.id}
              area={area}
              onChange={(updated) => replace(area.id, updated)}
            />
          ))}
        </section>
      )}

      {mastered.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-fl-muted-1 px-1 font-mono text-xs tracking-widest uppercase">
            {t('masteredSection', { count: mastered.length })}
          </h2>
          {mastered.map((area) => (
            <FocusAreaCard
              key={area.id}
              area={area}
              onChange={(updated) => replace(area.id, updated)}
            />
          ))}
        </section>
      )}
    </div>
  )
}
