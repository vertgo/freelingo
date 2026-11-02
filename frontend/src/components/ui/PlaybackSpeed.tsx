'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  MAX_PLAYBACK_RATE,
  MIN_PLAYBACK_RATE,
  PLAYBACK_RATE_STEP,
  setPlaybackRate,
} from '@/lib/audio-playback'
import { usePlaybackRate } from '@/hooks/usePlaybackRate'

function formatRate(rate: number) {
  return `${Math.round(rate * 100)}%`
}

/** A small speed chip that opens a 50–200% slider only when clicked. */
export function PlaybackSpeed({ className = '' }: { className?: string }) {
  const t = useTranslations('audioPlayer')
  const rate = usePlaybackRate()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const sliderId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={rootRef} className={`relative inline-flex ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={open ? sliderId : undefined}
        aria-label={t('speed')}
        title={t('speed')}
        className={`text-fl-hint focus-visible:outline-fl-fg border px-2 py-1 font-mono tracking-widest transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 ${
          open || rate !== 1
            ? 'border-fl-border-2 text-fl-fg'
            : 'border-fl-border text-fl-muted-2 hover:border-fl-border-2 hover:text-fl-fg'
        }`}
      >
        {formatRate(rate)}
      </button>
      {open && (
        <div className="border-fl-border bg-fl-surface absolute top-full left-0 z-20 mt-1 flex items-center gap-3 border px-3 py-2 shadow-sm">
          <input
            id={sliderId}
            type="range"
            min={MIN_PLAYBACK_RATE}
            max={MAX_PLAYBACK_RATE}
            step={PLAYBACK_RATE_STEP}
            value={rate}
            onChange={(event) => setPlaybackRate(Number(event.target.value))}
            aria-label={t('speed')}
            aria-valuetext={formatRate(rate)}
            className="accent-fl-accent w-32"
          />
          <span className="text-fl-hint text-fl-fg w-10 text-right font-mono tracking-widest">
            {formatRate(rate)}
          </span>
        </div>
      )}
    </div>
  )
}
