'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Loader2 } from 'lucide-react'
import {
  createDrill,
  gradeAnswer,
  recordPractice,
  type DrillItem,
  type FocusArea,
} from '@/lib/focus-areas'

type Grade = ReturnType<typeof gradeAnswer>

// Matches DRILL_PASS_RATIO in backend/app/services/focus_service.py.
const DRILL_PASS_RATIO = 0.8

/** A short fill-in-the-blank drill for one focus area. */
export function FocusDrill({
  area,
  onFinished,
  onClose,
}: {
  area: FocusArea
  onFinished: (updated: FocusArea) => void
  onClose: () => void
}) {
  const t = useTranslations('focus')
  const [items, setItems] = useState<DrillItem[] | null>(null)
  const [error, setError] = useState(false)
  const [index, setIndex] = useState(0)
  const [input, setInput] = useState('')
  const [grade, setGrade] = useState<Grade | null>(null)
  const [showHint, setShowHint] = useState(false)
  const [score, setScore] = useState(0)
  const [result, setResult] = useState<{ passed: boolean } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let canceled = false
    createDrill(area.id)
      .then((next) => !canceled && setItems(next))
      .catch(() => !canceled && setError(true))
    return () => {
      canceled = true
    }
  }, [area.id])

  useEffect(() => {
    if (items && grade === null) inputRef.current?.focus()
  }, [items, index, grade])

  if (error) {
    return (
      <div className="border-fl-error/40 text-fl-error mt-4 border px-4 py-3 font-mono text-xs">
        ✕ {t('drillError')}{' '}
        <button type="button" onClick={onClose} className="underline">
          {t('close')}
        </button>
      </div>
    )
  }
  if (!items) {
    return (
      <p className="text-fl-muted-2 mt-4 flex items-center gap-2 font-mono text-xs">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        {t('drillLoading')}
      </p>
    )
  }

  const item = items[index]
  const [before, after] = item.sentence.split('___', 2)

  function check(event: React.FormEvent) {
    event.preventDefault()
    if (grade !== null || !input.trim()) return
    const next = gradeAnswer(input, item)
    setGrade(next)
    if (next !== 'wrong') setScore((s) => s + 1)
  }

  async function next() {
    if (index + 1 < items!.length) {
      setIndex(index + 1)
      setInput('')
      setGrade(null)
      setShowHint(false)
      return
    }
    try {
      const updated = await recordPractice(area.id, score, items!.length)
      setResult({ passed: updated.correct_streak > area.correct_streak })
      onFinished(updated)
    } catch {
      setResult({ passed: false })
    }
  }

  if (result) {
    return (
      <div className="border-fl-border mt-4 space-y-3 border p-4 font-mono text-xs">
        <p className="text-fl-fg">
          {t('drillScore', { score, total: items.length })}
        </p>
        <p className="text-fl-muted-2">
          {result.passed
            ? t('drillPassed')
            : t('drillNotPassed', {
                needed: Math.ceil(items.length * DRILL_PASS_RATIO),
                total: items.length,
              })}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="text-fl-muted-1 underline hover:no-underline"
        >
          {t('close')}
        </button>
      </div>
    )
  }

  return (
    <div className="border-fl-border mt-4 space-y-3 border p-4">
      <p className="text-fl-muted-3 font-mono text-xs tracking-widest uppercase">
        {t('drillProgress', { current: index + 1, total: items.length })}
      </p>
      <form onSubmit={check} className="space-y-3">
        <label className="text-fl-fg block font-mono text-sm leading-loose">
          {before}
          <input
            ref={inputRef}
            aria-label={t('drillAnswer')}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={grade !== null}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            className="bg-fl-bg border-fl-border-2 text-fl-fg mx-1 w-32 border-b px-1 font-mono text-sm focus:outline-none"
          />
          {after}
        </label>
        {grade === null ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={!input.trim()}
              className="border-fl-accent bg-fl-accent text-fl-accent-fg border px-4 py-2 font-mono text-xs tracking-widest uppercase disabled:opacity-40"
            >
              {t('check')}
            </button>
            {item.hint && !showHint && (
              <button
                type="button"
                onClick={() => setShowHint(true)}
                className="text-fl-muted-2 border-fl-border hover:text-fl-fg border px-4 py-2 font-mono text-xs tracking-widest uppercase"
              >
                {t('hint')}
              </button>
            )}
          </div>
        ) : null}
      </form>
      {showHint && grade === null && (
        <p className="text-fl-muted-2 font-mono text-xs">{item.hint}</p>
      )}
      {grade !== null && (
        <div role="status" className="space-y-1 font-mono text-xs">
          <p className={grade === 'wrong' ? 'text-fl-error' : 'text-fl-fg'}>
            {grade === 'correct'
              ? `✓ ${t('correct')}`
              : grade === 'accents'
                ? `✓ ${t('correctAccents', { answer: item.answer })}`
                : `✕ ${t('wrong', { answer: item.answer })}`}
          </p>
          {item.translation && (
            <p className="text-fl-muted-3">{item.translation}</p>
          )}
          <button
            type="button"
            onClick={next}
            className="border-fl-border text-fl-fg hover:border-fl-border-2 mt-2 border px-4 py-2 tracking-widest uppercase"
          >
            {index + 1 < items.length ? t('next') : t('finish')}
          </button>
        </div>
      )}
    </div>
  )
}
