// Only one audio source sounds at a time: starting one stops whichever was
// active. Shared by the single-text AudioPlayer and the lesson reader.
let stopActivePlayer: (() => void) | null = null

/** Stops whatever audio is playing and makes `stop` the active player. */
export function claimAudioPlayback(stop: () => void) {
  if (stopActivePlayer && stopActivePlayer !== stop) stopActivePlayer()
  stopActivePlayer = stop
}

export function releaseAudioPlayback(stop: () => void) {
  if (stopActivePlayer === stop) stopActivePlayer = null
}

/** The TTS voice the learner picked in Settings, if any. */
export function storedVoice() {
  try {
    return typeof window !== 'undefined'
      ? (localStorage.getItem('tts_voice') ?? undefined)
      : undefined
  } catch {
    return undefined
  }
}

// Playback speed for TTS audio, chosen with the small speed control and kept
// in this browser. The browser stretches the audio without changing its pitch.
export const MIN_PLAYBACK_RATE = 0.5
export const MAX_PLAYBACK_RATE = 2
export const PLAYBACK_RATE_STEP = 0.05
const RATE_KEY = 'tts_playback_rate'

let playbackRate: number | null = null
let rateAudio: HTMLAudioElement | null = null
const rateListeners = new Set<() => void>()

function normalizeRate(value: number) {
  if (!Number.isFinite(value)) return 1
  // Round to two decimals so steps stay exact (1.1, not 1.1000000000000001).
  const stepped =
    Math.round(
      Math.round(value / PLAYBACK_RATE_STEP) * PLAYBACK_RATE_STEP * 100
    ) / 100
  return Math.min(MAX_PLAYBACK_RATE, Math.max(MIN_PLAYBACK_RATE, stepped))
}

export function getPlaybackRate(): number {
  if (playbackRate === null) {
    let stored: string | null = null
    try {
      stored =
        typeof window !== 'undefined' ? localStorage.getItem(RATE_KEY) : null
    } catch {
      stored = null
    }
    playbackRate = stored === null ? 1 : normalizeRate(Number(stored))
  }
  return playbackRate
}

/** Saves the speed and applies it to the audio playing right now. */
export function setPlaybackRate(value: number) {
  const next = normalizeRate(value)
  playbackRate = next
  try {
    localStorage.setItem(RATE_KEY, String(next))
  } catch {
    // Speed still applies for this page; it just isn't remembered.
  }
  if (rateAudio) rateAudio.playbackRate = next
  rateListeners.forEach((listener) => listener())
}

export function subscribePlaybackRate(listener: () => void) {
  rateListeners.add(listener)
  return () => {
    rateListeners.delete(listener)
  }
}

/** Plays `audio` at the chosen speed; later speed changes reach it live. */
export function applyPlaybackRate(audio: HTMLAudioElement) {
  audio.preservesPitch = true
  audio.playbackRate = getPlaybackRate()
  rateAudio = audio
}
