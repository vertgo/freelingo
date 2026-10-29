import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { FocusDrill } from '@/components/focus/FocusDrill'
import type { FocusArea } from '@/lib/focus-areas'

const mocks = vi.hoisted(() => ({
  createDrill: vi.fn(),
  recordPractice: vi.fn(),
}))

vi.mock('@/lib/focus-areas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/focus-areas')>()),
  createDrill: mocks.createDrill,
  recordPractice: mocks.recordPractice,
}))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, values?: Record<string, unknown>) =>
    values ? `${key}(${Object.values(values).join(',')})` : key,
}))

const area: FocusArea = {
  id: 7,
  concept: 'Ser vs. estar',
  category: 'grammar',
  explanation: '',
  examples: [],
  occurrences: 2,
  correct_streak: 0,
  mastery_streak: 3,
  status: 'active',
  first_seen_at: '2026-10-01T00:00:00',
  last_seen_at: '2026-10-02T00:00:00',
  mastered_at: null,
}

const items = [
  {
    sentence: 'Hoy ___ (ser / estar) cansado.',
    answer: 'estoy',
    accepted: [],
    hint: 'Temporary state',
    translation: 'Today I am tired.',
  },
  {
    sentence: 'Ella ___ (ser / estar) doctora.',
    answer: 'es',
    accepted: [],
    hint: '',
    translation: 'She is a doctor.',
  },
]

function answer(value: string) {
  fireEvent.change(screen.getByLabelText('drillAnswer'), {
    target: { value },
  })
  fireEvent.click(screen.getByRole('button', { name: 'check' }))
}

describe('FocusDrill', () => {
  beforeEach(() => {
    mocks.createDrill.mockReset().mockResolvedValue(items)
    mocks.recordPractice.mockReset()
  })

  it('grades each blank and records a passing drill', async () => {
    mocks.recordPractice.mockResolvedValue({ ...area, correct_streak: 1 })
    const onFinished = vi.fn()
    render(<FocusDrill area={area} onFinished={onFinished} onClose={vi.fn()} />)

    expect(await screen.findByText(/Hoy/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'hint' }))
    expect(screen.getByText('Temporary state')).toBeInTheDocument()
    answer('estoy')
    expect(screen.getByRole('status')).toHaveTextContent('correct')
    expect(screen.getByText('Today I am tired.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'next' }))

    answer('Es')
    fireEvent.click(screen.getByRole('button', { name: 'finish' }))

    expect(await screen.findByText('drillScore(2,2)')).toBeInTheDocument()
    expect(screen.getByText('drillPassed')).toBeInTheDocument()
    expect(mocks.recordPractice).toHaveBeenCalledWith(7, 2, 2)
    expect(onFinished).toHaveBeenCalledWith(
      expect.objectContaining({ correct_streak: 1 })
    )
  })

  it('shows the answer when wrong and explains a failed drill', async () => {
    mocks.recordPractice.mockResolvedValue(area)
    render(<FocusDrill area={area} onFinished={vi.fn()} onClose={vi.fn()} />)
    await screen.findByText(/Hoy/)
    answer('soy')
    expect(screen.getByRole('status')).toHaveTextContent('wrong(estoy)')
    fireEvent.click(screen.getByRole('button', { name: 'next' }))
    answer('es')
    fireEvent.click(screen.getByRole('button', { name: 'finish' }))
    expect(await screen.findByText('drillNotPassed(2,2)')).toBeInTheDocument()
  })

  it('reports a drill that could not be created', async () => {
    mocks.createDrill.mockRejectedValue(new Error('boom'))
    render(<FocusDrill area={area} onFinished={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText(/drillError/)).toBeInTheDocument()
  })
})
