'use client'

import { useTranslations } from 'next-intl'
import {
  Loader2,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  Square,
} from 'lucide-react'
import type { useLessonReader } from '@/hooks/useLessonReader'

type LessonReader = ReturnType<typeof useLessonReader>

const buttonClass =
  'border-fl-border text-fl-muted-1 hover:border-fl-border-2 hover:text-fl-fg focus-visible:outline-fl-fg flex items-center gap-2 border px-3 py-2 font-mono text-xs tracking-widest uppercase transition-colors focus-visible:outline-2 focus-visible:outline-offset-2'

/** "Play lesson" controls: reads the lesson's target-language text aloud. */
export function LessonReadAloud({ reader }: { reader: LessonReader }) {
  const t = useTranslations('lesson.readAloud')
  if (reader.blocks === 0) return null

  if (reader.status === 'idle') {
    return (
      <button
        type="button"
        onClick={() => void reader.play(0)}
        className={buttonClass}
      >
        <Play className="size-4" aria-hidden="true" />
        {t('play')}
      </button>
    )
  }

  const isPlaying = reader.status === 'playing'
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="group"
      aria-label={t('controls')}
    >
      <button
        type="button"
        onClick={reader.previous}
        className={buttonClass}
        aria-label={t('previous')}
        title={t('previous')}
      >
        <SkipBack className="size-4" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={isPlaying ? reader.pause : reader.resume}
        disabled={reader.status === 'loading'}
        className={buttonClass}
        aria-label={isPlaying ? t('pause') : t('resume')}
        title={isPlaying ? t('pause') : t('resume')}
      >
        {reader.status === 'loading' ? (
          <Loader2
            className="size-4 motion-safe:animate-spin"
            aria-hidden="true"
          />
        ) : isPlaying ? (
          <Pause className="size-4" aria-hidden="true" />
        ) : (
          <Play className="size-4" aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        onClick={reader.next}
        className={buttonClass}
        aria-label={t('next')}
        title={t('next')}
      >
        <SkipForward className="size-4" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={reader.stop}
        className={buttonClass}
        aria-label={t('stop')}
        title={t('stop')}
      >
        <Square className="size-4" aria-hidden="true" />
      </button>
      <span
        className="text-fl-hint text-fl-muted-2 font-mono tracking-widest"
        aria-live="polite"
      >
        {reader.status === 'error'
          ? t('error')
          : t('progress', { current: reader.block, total: reader.blocks })}
      </span>
    </div>
  )
}
