import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiKeySection } from '@/components/settings/ApiKeySection'

const mockApiFetch = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', () => ({ apiFetch: mockApiFetch }))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) =>
    values ? `${key}(${Object.values(values).join(',')})` : key,
}))

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

const noKey = {
  enabled: true,
  has_key: false,
  hint: null,
  services: ['llm', 'tts'],
}

describe('ApiKeySection', () => {
  beforeEach(() => mockApiFetch.mockReset())

  it('renders nothing when the server does not allow personal keys', async () => {
    mockApiFetch.mockResolvedValue(json({ ...noKey, enabled: false }))
    const { container } = render(<ApiKeySection />)
    await waitFor(() => expect(mockApiFetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('saves a key and shows its hint, never the key', async () => {
    mockApiFetch
      .mockResolvedValueOnce(json(noKey))
      .mockResolvedValueOnce(
        json({ ...noKey, has_key: true, hint: '…abcd', warning: null })
      )
    render(<ApiKeySection />)
    expect(await screen.findByText('usingServer')).toBeInTheDocument()
    expect(screen.getByText('usedFor(llm · tts)')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('label'), {
      target: { value: '  AIzaSecret-abcd ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'save' }))

    expect(await screen.findByText('usingOwn(…abcd)')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('saved')
    expect(mockApiFetch).toHaveBeenLastCalledWith('/api/auth/me/api-key', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: 'AIzaSecret-abcd' }),
    })
    expect(screen.getByLabelText('label')).toHaveValue('')
  })

  it('explains why a key was rejected', async () => {
    mockApiFetch
      .mockResolvedValueOnce(json(noKey))
      .mockResolvedValueOnce(json({ reason: 'auth' }, 400))
    render(<ApiKeySection />)
    fireEvent.change(await screen.findByLabelText('label'), {
      target: { value: 'AIzaBadKey123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('rejected.auth')
    expect(screen.getByText('usingServer')).toBeInTheDocument()
  })

  it('warns when a saved key is out of quota', async () => {
    mockApiFetch
      .mockResolvedValueOnce(json(noKey))
      .mockResolvedValueOnce(
        json({ ...noKey, has_key: true, hint: '…abcd', warning: 'quota' })
      )
    render(<ApiKeySection />)
    fireEvent.change(await screen.findByLabelText('label'), {
      target: { value: 'AIzaKey-abcd' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'savedWarning.quota'
    )
  })

  it('removes a saved key', async () => {
    mockApiFetch
      .mockResolvedValueOnce(json({ ...noKey, has_key: true, hint: '…abcd' }))
      .mockResolvedValueOnce(json(noKey))
    render(<ApiKeySection />)
    fireEvent.click(await screen.findByRole('button', { name: 'remove' }))
    expect(await screen.findByText('usingServer')).toBeInTheDocument()
    expect(mockApiFetch).toHaveBeenLastCalledWith('/api/auth/me/api-key', {
      method: 'DELETE',
    })
  })
})
