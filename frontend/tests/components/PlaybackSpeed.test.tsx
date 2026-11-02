import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import { PlaybackSpeed } from '@/components/ui/PlaybackSpeed'
import { getPlaybackRate, setPlaybackRate } from '@/lib/audio-playback'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

describe('PlaybackSpeed', () => {
  beforeEach(() => {
    act(() => setPlaybackRate(1))
  })

  it('shows only the speed until clicked', () => {
    render(<PlaybackSpeed />)
    expect(screen.getByRole('button', { name: 'speed' })).toHaveTextContent(
      '100%'
    )
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()
  })

  it('opens a 50–200% slider in 5% steps and applies the change', () => {
    render(<PlaybackSpeed />)
    fireEvent.click(screen.getByRole('button', { name: 'speed' }))
    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('min', '0.5')
    expect(slider).toHaveAttribute('max', '2')
    expect(slider).toHaveAttribute('step', '0.05')
    fireEvent.change(slider, { target: { value: '0.85' } })
    expect(getPlaybackRate()).toBe(0.85)
    expect(screen.getByRole('button', { name: 'speed' })).toHaveTextContent(
      '85%'
    )
  })

  it('closes on Escape and on a click outside', () => {
    render(
      <div>
        <PlaybackSpeed />
        <p>outside</p>
      </div>
    )
    const chip = screen.getByRole('button', { name: 'speed' })
    fireEvent.click(chip)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()
    fireEvent.click(chip)
    fireEvent.pointerDown(screen.getByText('outside'))
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()
  })
})
