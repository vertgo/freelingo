import { describe, expect, it } from 'vitest'

import { gradeAnswer, parseServerDate } from '@/lib/focus-areas'

describe('gradeAnswer', () => {
  const item = { answer: 'estás', accepted: ['andas'] }

  it('accepts the answer and alternatives, ignoring case, spaces and punctuation', () => {
    expect(gradeAnswer('  Estás. ', item)).toBe('correct')
    expect(gradeAnswer('andas', item)).toBe('correct')
  })

  it('flags a right answer that is missing accents', () => {
    expect(gradeAnswer('estas', item)).toBe('accents')
  })

  it('rejects wrong and empty answers', () => {
    expect(gradeAnswer('eres', item)).toBe('wrong')
    expect(gradeAnswer('   ', item)).toBe('wrong')
  })
})

describe('parseServerDate', () => {
  it('treats naive backend timestamps as UTC', () => {
    expect(parseServerDate('2026-10-29T12:00:00').toISOString()).toBe(
      '2026-10-29T12:00:00.000Z'
    )
    expect(parseServerDate('2026-10-29T12:00:00+00:00').toISOString()).toBe(
      '2026-10-29T12:00:00.000Z'
    )
  })
})
