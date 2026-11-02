import { beforeEach, describe, expect, it, vi } from 'vitest'

async function load() {
  vi.resetModules()
  return import('@/lib/audio-playback')
}

describe('playback rate', () => {
  beforeEach(() => localStorage.clear())

  it('defaults to normal speed', async () => {
    const { getPlaybackRate } = await load()
    expect(getPlaybackRate()).toBe(1)
  })

  it('remembers the chosen speed in this browser', async () => {
    const first = await load()
    first.setPlaybackRate(1.5)
    const second = await load()
    expect(second.getPlaybackRate()).toBe(1.5)
  })

  it('keeps the speed between 50% and 200% in 5% steps', async () => {
    const { setPlaybackRate, getPlaybackRate } = await load()
    setPlaybackRate(3)
    expect(getPlaybackRate()).toBe(2)
    setPlaybackRate(0.1)
    expect(getPlaybackRate()).toBe(0.5)
    setPlaybackRate(1.32)
    expect(getPlaybackRate()).toBe(1.3)
    setPlaybackRate(0.85)
    expect(getPlaybackRate()).toBe(0.85)
    setPlaybackRate(1.1)
    expect(getPlaybackRate()).toBe(1.1)
    localStorage.setItem('tts_playback_rate', 'nonsense')
    const reloaded = await load()
    expect(reloaded.getPlaybackRate()).toBe(1)
  })

  it('plays new audio at the speed and changes the current audio live', async () => {
    const { applyPlaybackRate, setPlaybackRate } = await load()
    setPlaybackRate(0.75)
    const audio = {} as HTMLAudioElement
    applyPlaybackRate(audio)
    expect(audio.playbackRate).toBe(0.75)
    expect(audio.preservesPitch).toBe(true)
    setPlaybackRate(1.25)
    expect(audio.playbackRate).toBe(1.25)
  })
})
