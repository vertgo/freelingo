import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FocusPage from '@/app/(app)/focus/page'
import type { FocusArea } from '@/lib/focus-areas'

const mocks = vi.hoisted(() => ({
  fetchFocusAreas: vi.fn(),
  setFocusAreaStatus: vi.fn(),
  createFocusFlashcards: vi.fn(),
}))

vi.mock('@/lib/focus-areas', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/focus-areas')>()),
  fetchFocusAreas: mocks.fetchFocusAreas,
  setFocusAreaStatus: mocks.setFocusAreaStatus,
  createFocusFlashcards: mocks.createFocusFlashcards,
  createDrill: vi.fn(() => new Promise(() => {})),
}))
vi.mock('next-intl', () => ({
  useLocale: () => 'en',
  useTranslations: () => (key: string, values?: Record<string, unknown>) =>
    values ? `${key}(${Object.values(values).join(',')})` : key,
}))

function area(overrides: Partial<FocusArea>): FocusArea {
  return {
    id: 1,
    concept: 'Ser vs. estar',
    category: 'grammar',
    explanation: 'Estar for temporary states.',
    examples: [{ said: 'Soy cansado', correction: 'Estoy cansado' }],
    occurrences: 3,
    correct_streak: 1,
    mastery_streak: 3,
    status: 'active',
    first_seen_at: '2026-10-01T00:00:00',
    last_seen_at: '2026-10-20T00:00:00',
    mastered_at: null,
    ...overrides,
  }
}

describe('FocusPage', () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset())
  })

  it('explains how focus areas appear when there are none', async () => {
    mocks.fetchFocusAreas.mockResolvedValue([])
    render(<FocusPage />)
    expect(await screen.findByText('empty')).toBeInTheDocument()
  })

  it('lists active and mastered areas with examples and progress', async () => {
    mocks.fetchFocusAreas.mockResolvedValue([
      area({}),
      area({ id: 2, concept: 'Gender agreement', status: 'mastered' }),
    ])
    render(<FocusPage />)
    const card = (await screen.findByText('Ser vs. estar')).closest('article')!
    expect(within(card).getByText('Soy cansado')).toBeInTheDocument()
    expect(within(card).getByText('Estoy cansado')).toBeInTheDocument()
    expect(within(card).getByText('streak(1,3)')).toBeInTheDocument()
    expect(screen.getByText('masteredSection(1)')).toBeInTheDocument()
  })

  it('makes flashcards and dismisses an area', async () => {
    mocks.fetchFocusAreas.mockResolvedValue([area({})])
    mocks.createFocusFlashcards.mockResolvedValue(3)
    mocks.setFocusAreaStatus.mockResolvedValue(area({ status: 'dismissed' }))
    render(<FocusPage />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'makeFlashcards' })
    )
    expect(await screen.findByRole('status')).toHaveTextContent(
      'flashcardsCreated(3)'
    )
    fireEvent.click(screen.getByRole('button', { name: 'dismiss' }))
    expect(await screen.findByText('empty')).toBeInTheDocument()
    expect(mocks.setFocusAreaStatus).toHaveBeenCalledWith(1, 'dismissed')
  })

  it('opens a drill from the card', async () => {
    mocks.fetchFocusAreas.mockResolvedValue([area({})])
    render(<FocusPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'practice' }))
    expect(await screen.findByText('drillLoading')).toBeInTheDocument()
  })
})
