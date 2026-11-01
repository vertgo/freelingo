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
