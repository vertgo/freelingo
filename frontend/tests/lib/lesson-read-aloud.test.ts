import { describe, expect, it } from 'vitest'
import {
  MAX_CHUNK_CHARS,
  blockStart,
  buildLessonReadAloud,
  chunkText,
  nextBlockStart,
  previousBlockStart,
} from '@/lib/lesson-read-aloud'

const lesson = {
  title: 'Ser y estar',
  explanation: {
    text: 'Usamos ser para el origen. Usamos estar para los estados.',
    key_points: ['Soy de Monterrey.', '  '],
    examples: [
      { sentence: 'Estoy cansado.', note: 'A temporary state.' },
      { note: 'No sentence here.' },
    ],
  },
  vocabulary: [
    {
      word: 'carro',
      definition: 'Vehículo de cuatro ruedas.',
      translation: 'car',
      example: 'Mi carro es rojo.',
      example_translation: 'My car is red.',
      note: 'Spain says coche.',
    },
  ],
}

describe('buildLessonReadAloud', () => {
  it('reads the target-language content in page order', () => {
    expect(buildLessonReadAloud(lesson)).toEqual([
      { key: 'title', text: 'Ser y estar' },
      {
        key: 'explanation',
        text: 'Usamos ser para el origen. Usamos estar para los estados.',
      },
      { key: 'key-point-0', text: 'Soy de Monterrey.' },
      { key: 'example-0', text: 'Estoy cansado.' },
      { key: 'vocab-0', text: 'carro' },
      { key: 'vocab-0', text: 'Vehículo de cuatro ruedas.' },
      { key: 'vocab-0', text: 'Mi carro es rojo.' },
    ])
  })

  it('never reads translations or notes', () => {
    const texts = buildLessonReadAloud(lesson).map((part) => part.text)
    expect(texts.join(' ')).not.toMatch(/car is red|coche|temporary/)
  })

  it('returns nothing for a lesson without content', () => {
    expect(buildLessonReadAloud({})).toEqual([])
  })
})

describe('chunkText', () => {
  it('keeps short text whole and collapses whitespace', () => {
    expect(chunkText('  Hola.\n  ¿Qué tal?  ')).toEqual(['Hola. ¿Qué tal?'])
  })

  it('splits long text at sentence ends under the limit', () => {
    const sentence = 'Esta es una oración bastante larga para la prueba. '
    const chunks = chunkText(sentence.repeat(30))
    expect(chunks.length).toBeGreaterThan(1)
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(MAX_CHUNK_CHARS)
      expect(chunk.endsWith('.')).toBe(true)
    }
    expect(chunks.join(' ')).toBe(sentence.repeat(30).trim())
  })
})

describe('block navigation', () => {
  const parts = buildLessonReadAloud(lesson)

  it('skips forward a whole block', () => {
    expect(nextBlockStart(parts, -1)).toBe(0)
    expect(nextBlockStart(parts, 3)).toBe(4)
    expect(nextBlockStart(parts, 4)).toBe(parts.length)
  })

  it('skips back to the start of the previous block', () => {
    expect(previousBlockStart(parts, 6)).toBe(3)
    expect(previousBlockStart(parts, 0)).toBe(0)
    expect(blockStart(parts, 6)).toBe(4)
  })
})
