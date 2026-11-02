'use client'

import { useSyncExternalStore } from 'react'
import { getPlaybackRate, subscribePlaybackRate } from '@/lib/audio-playback'

/** The TTS playback speed, updated whenever the learner changes it. */
export function usePlaybackRate() {
  return useSyncExternalStore(subscribePlaybackRate, getPlaybackRate, () => 1)
}
