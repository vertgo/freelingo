import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLessonReader } from '@/hooks/useLessonReader'
import { claimAudioPlayback } from '@/lib/audio-playback'
import type { ReadAloudPart } from '@/lib/lesson-read-aloud'

const apiFetch = vi.fn()
vi.mock('@/lib/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}))

interface AudioMock {
  url: string
  play: ReturnType<typeof vi.fn>
  pause: ReturnType<typeof vi.fn>
  onended: (() => void) | null
  onerror: (() => void) | null
}

let audios: AudioMock[] = []

const parts: ReadAloudPart[] = [
  { key: 'title', text: 'Ser y estar' },
  { key: 'vocab-0', text: 'carro' },
  { key: 'vocab-0', text: 'Mi carro es rojo.' },
  { key: 'example-0', text: 'Estoy cansado.' },
]

function ttsBody(call: unknown[]) {
  return JSON.parse((call[1] as { body: string }).body)
}

describe('useLessonReader', () => {
  beforeEach(() => {
    audios = []
    apiFetch.mockReset()
    apiFetch.mockImplementation(() =>
      Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob()) })
    )
    let n = 0
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:${++n}`)
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    ;(globalThis as any).Audio = function (url: string) {
      const audio: AudioMock = {
        url,
        play: vi.fn().mockResolvedValue(undefined),
        pause: vi.fn(),
        onended: null,
        onerror: null,
      }
      audios.push(audio)
      return audio
    }
  })

  it('plays every part in order with the plan id, then stops', async () => {
    const { result } = renderHook(() => useLessonReader(parts, 7))
    expect(result.current.blocks).toBe(3)

    await act(() => result.current.play(0))
    expect(result.current.status).toBe('playing')
    expect(result.current.activeKey).toBe('title')
    expect(ttsBody(apiFetch.mock.calls[0])).toMatchObject({
      text: 'Ser y estar',
      study_plan_id: 7,
    })
    // The next part is fetched ahead while the first one plays.
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2))

    for (let i = 0; i < parts.length; i++) {
      await act(async () => audios[i].onended?.())
      await waitFor(() =>
        expect(audios.length).toBe(Math.min(i + 2, parts.length))
      )
    }
    expect(result.current.status).toBe('idle')
    expect(result.current.activeKey).toBeNull()
    expect(apiFetch).toHaveBeenCalledTimes(parts.length)
  })

  it('skips whole blocks and reuses fetched audio', async () => {
    const { result } = renderHook(() => useLessonReader(parts))
    await act(() => result.current.play(0))
    await act(async () => result.current.next())
    await waitFor(() => expect(result.current.activeKey).toBe('vocab-0'))
    await act(async () => result.current.next())
    await waitFor(() => expect(result.current.activeKey).toBe('example-0'))
    expect(result.current.block).toBe(3)

    await act(async () => result.current.previous())
    await waitFor(() => expect(result.current.activeKey).toBe('vocab-0'))
    const texts = apiFetch.mock.calls.map((call) => ttsBody(call).text)
    expect(texts.filter((text) => text === 'carro')).toHaveLength(1)
  })

  it('pauses and resumes the same audio', async () => {
    const { result } = renderHook(() => useLessonReader(parts))
    await act(() => result.current.play(0))
    act(() => result.current.pause())
    expect(result.current.status).toBe('paused')
    expect(audios[0].pause).toHaveBeenCalled()

    await act(async () => result.current.resume())
    expect(result.current.status).toBe('playing')
    expect(audios[0].play).toHaveBeenCalledTimes(2)
    expect(audios).toHaveLength(1)
  })

  it('shows an error when a part cannot be fetched, and retries on resume', async () => {
    apiFetch.mockResolvedValueOnce({ ok: false, status: 502 })
    const { result } = renderHook(() => useLessonReader(parts))
    await act(() => result.current.play(0))
    expect(result.current.status).toBe('error')
    expect(result.current.activeKey).toBe('title')

    await act(async () => result.current.resume())
    await waitFor(() => expect(result.current.status).toBe('playing'))
    expect(audios).toHaveLength(1)
  })

  it('stops when another audio player starts', async () => {
    const { result } = renderHook(() => useLessonReader(parts))
    await act(() => result.current.play(0))
    act(() => claimAudioPlayback(() => {}))
    expect(result.current.status).toBe('idle')
    expect(audios[0].pause).toHaveBeenCalled()
  })
})
