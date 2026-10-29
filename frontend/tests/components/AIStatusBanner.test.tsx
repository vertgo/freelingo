import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AIStatusBanner } from '@/components/AIStatusBanner'
import { reportAIError } from '@/lib/ai-errors'
import { useAuthStore, type User } from '@/store/auth'

const mockApiFetch = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', () => ({ apiFetch: mockApiFetch }))
vi.mock('next/navigation', () => ({ usePathname: () => '/lesson/1' }))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) =>
    values ? `${key}(${Object.values(values).join(',')})` : key,
}))

const baseUser = {
  id: 1,
  username: 'learner',
  displayName: 'Learner',
  role: 'user',
  conversation_max_duration: 300,
  conversation_inactivity_timeout: 30,
} satisfies User

function statusResponse(failure: unknown, ownKeyServices: string[] = []) {
  return new Response(
    JSON.stringify({ failure, own_key_services: ownKeyServices }),
    { status: 200 }
  )
}

describe('AIStatusBanner', () => {
  beforeEach(() => {
    mockApiFetch.mockReset()
    sessionStorage.clear()
    useAuthStore.setState({ user: baseUser })
  })

  it('stays hidden when the server reports no failure', async () => {
    mockApiFetch.mockResolvedValue(statusResponse(null))
    render(<AIStatusBanner />)
    await waitFor(() =>
      expect(mockApiFetch).toHaveBeenCalledWith('/api/ai-status')
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('explains a failure reported by the page', async () => {
    mockApiFetch.mockResolvedValue(statusResponse(null))
    render(<AIStatusBanner />)
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalled())

    act(() => reportAIError('quota', 'tts'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('title(services.tts)')
    expect(alert).toHaveTextContent('reasons.quota')
    expect(alert).toHaveTextContent('userHint')
  })

  it('shows the server failure with admin hints and detail', async () => {
    useAuthStore.setState({ user: { ...baseUser, role: 'admin' } })
    mockApiFetch.mockResolvedValue(
      statusResponse({
        service: 'llm',
        code: 'auth',
        detail: 'API key not valid',
        at: 100,
      })
    )
    render(<AIStatusBanner />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('reasons.auth')
    expect(alert).toHaveTextContent('adminHints.auth')
    expect(alert).toHaveTextContent('API key not valid')
  })

  it('stays dismissed for that failure but returns for a newer one', async () => {
    mockApiFetch.mockResolvedValue(
      statusResponse({ service: 'llm', code: 'overloaded', at: 100 })
    )
    const { unmount } = render(<AIStatusBanner />)
    fireEvent.click(await screen.findByRole('button', { name: 'dismiss' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    unmount()

    render(<AIStatusBanner />)
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    act(() => reportAIError('timeout', 'llm'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'reasons.timeout'
    )
  })

  it("points learners at their own key when it's the one failing", async () => {
    useAuthStore.setState({ user: { ...baseUser, role: 'admin' } })
    mockApiFetch.mockResolvedValue(statusResponse(null, ['llm', 'tts']))
    render(<AIStatusBanner />)
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalled())

    act(() => reportAIError('auth', 'llm'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('ownKey.auth')
    expect(alert).toHaveTextContent('ownKey.hint')
    expect(alert).not.toHaveTextContent('adminHints')
  })

  it('ignores unknown codes', async () => {
    mockApiFetch.mockResolvedValue(statusResponse(null))
    render(<AIStatusBanner />)
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalled())
    act(() => reportAIError('not-a-code', 'llm'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
