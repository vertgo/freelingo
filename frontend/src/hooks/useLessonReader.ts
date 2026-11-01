'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import {
  nextBlockStart,
  previousBlockStart,
  type ReadAloudPart,
} from '@/lib/lesson-read-aloud'
import {
  claimAudioPlayback,
  releaseAudioPlayback,
  storedVoice,
} from '@/lib/audio-playback'

const TTS_TIMEOUT_MS = 15_000

export type LessonReaderStatus =
  'idle' | 'loading' | 'playing' | 'paused' | 'error'

/**
 * Plays a lesson's read-aloud parts in order through the TTS endpoint.
 * Each part is fetched once and kept for replays; the next part is
 * prefetched while the current one plays.
 */
export function useLessonReader(parts: ReadAloudPart[], studyPlanId?: number) {
  const [status, setStatus] = useState<LessonReaderStatus>('idle')
  const [index, setIndex] = useState(-1)
  const partsRef = useRef(parts)
  const studyPlanRef = useRef(studyPlanId)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlsRef = useRef(new Map<string, Promise<string>>())
  const controllersRef = useRef(new Set<AbortController>())
  // Bumped on every jump or stop so stale playback callbacks do nothing.
  const generationRef = useRef(0)
  const stopHandleRef = useRef<() => void>(() => {})
  // Lets a finished part start the next one without playFrom calling itself.
  const playFromRef = useRef<(start: number) => Promise<void>>(async () => {})

  const fetchUrl = useCallback((text: string) => {
    const cached = urlsRef.current.get(text)
    if (cached) return cached
    const controller = new AbortController()
    controllersRef.current.add(controller)
    const timeoutId = setTimeout(() => controller.abort(), TTS_TIMEOUT_MS)
    const promise = apiFetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        voice: storedVoice(),
        study_plan_id: studyPlanRef.current,
      }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`TTS error ${res.status}`)
        return URL.createObjectURL(await res.blob())
      })
      .finally(() => {
        clearTimeout(timeoutId)
        controllersRef.current.delete(controller)
      })
    promise.catch(() => urlsRef.current.delete(text))
    urlsRef.current.set(text, promise)
    return promise
  }, [])

  const stopAudio = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.onended = null
    audio.onerror = null
    audio.pause()
    audioRef.current = null
  }, [])

  const stop = useCallback(() => {
    generationRef.current += 1
    stopAudio()
    releaseAudioPlayback(stopHandleRef.current)
    setIndex(-1)
    setStatus('idle')
  }, [stopAudio])

  const playFrom = useCallback(
    async (start: number) => {
      const all = partsRef.current
      if (start < 0 || start >= all.length) {
        stop()
        return
      }
      const generation = ++generationRef.current
      stopAudio()
      claimAudioPlayback(stopHandleRef.current)
      setIndex(start)
      setStatus('loading')
      try {
        const url = await fetchUrl(all[start].text)
        if (generation !== generationRef.current) return
        const audio = new Audio(url)
        audioRef.current = audio
        audio.onended = () => {
          if (generation === generationRef.current)
            void playFromRef.current(start + 1)
        }
        audio.onerror = () => {
          if (generation === generationRef.current) setStatus('error')
        }
        await audio.play()
        if (generation !== generationRef.current) return
        setStatus('playing')
        const following = all[start + 1]
        if (following) fetchUrl(following.text).catch(() => {})
      } catch {
        if (generation === generationRef.current) {
          stopAudio()
          setStatus('error')
        }
      }
    },
    [fetchUrl, stop, stopAudio]
  )

  const pause = useCallback(() => {
    if (!audioRef.current) return
    audioRef.current.pause()
    setStatus('paused')
  }, [])

  const resume = useCallback(() => {
    const audio = audioRef.current
    if (status === 'paused' && audio) {
      claimAudioPlayback(stopHandleRef.current)
      void audio.play().then(
        () => setStatus('playing'),
        () => setStatus('error')
      )
      return
    }
    void playFrom(index >= 0 ? index : 0)
  }, [index, playFrom, status])

  useEffect(() => {
    stopHandleRef.current = stop
  }, [stop])

  useEffect(() => {
    playFromRef.current = playFrom
  }, [playFrom])

  useEffect(() => {
    studyPlanRef.current = studyPlanId
  }, [studyPlanId])

  // New lesson content: stop and forget audio fetched for the old parts.
  useEffect(() => {
    partsRef.current = parts
    const urls = urlsRef.current
    const controllers = controllersRef.current
    return () => {
      stopHandleRef.current()
      controllers.forEach((controller) => controller.abort())
      controllers.clear()
      urls.forEach((promise) =>
        promise.then((url) => URL.revokeObjectURL(url)).catch(() => {})
      )
      urls.clear()
    }
  }, [parts])

  const activeKey = index >= 0 ? (parts[index]?.key ?? null) : null
  const blockKeys = [...new Set(parts.map((part) => part.key))]

  return {
    status,
    activeKey,
    /** 1-based position of the block being read, 0 when stopped. */
    block: activeKey ? blockKeys.indexOf(activeKey) + 1 : 0,
    blocks: blockKeys.length,
    play: playFrom,
    pause,
    resume,
    stop,
    next: () => void playFrom(nextBlockStart(parts, index)),
    previous: () => void playFrom(previousBlockStart(parts, index)),
    playBlock: (key: string) => {
      const start = parts.findIndex((part) => part.key === key)
      if (start >= 0) void playFrom(start)
    },
  }
}
