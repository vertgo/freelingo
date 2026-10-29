import { apiFetch } from '@/lib/api'

/** A mistake the learner keeps making, found by reviewing their conversations. */
export interface FocusArea {
  id: number
  concept: string
  category: string
  explanation: string
  examples: { said: string; correction: string }[]
  occurrences: number
  correct_streak: number
  mastery_streak: number
  status: 'active' | 'mastered' | 'dismissed'
  first_seen_at: string
  last_seen_at: string
  mastered_at: string | null
}

export interface DrillItem {
  sentence: string
  answer: string
  accepted: string[]
  hint: string
  translation: string
}

export class FocusApiError extends Error {
  constructor(public readonly status: number) {
    super(`focus_api_error_${status}`)
  }
}

async function parseOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) throw new FocusApiError(response.status)
  return response.json() as Promise<T>
}

export async function fetchFocusAreas(): Promise<FocusArea[]> {
  return parseOrThrow(await apiFetch('/api/focus-areas'))
}

export async function setFocusAreaStatus(
  id: number,
  status: FocusArea['status']
): Promise<FocusArea> {
  return parseOrThrow(
    await apiFetch(`/api/focus-areas/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
  )
}

export async function createDrill(id: number): Promise<DrillItem[]> {
  const body = await parseOrThrow<{ items: DrillItem[] }>(
    await apiFetch(`/api/focus-areas/${id}/drill`, { method: 'POST' })
  )
  return body.items
}

export async function recordPractice(
  id: number,
  correct: number,
  total: number
): Promise<FocusArea> {
  return parseOrThrow(
    await apiFetch(`/api/focus-areas/${id}/practice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ correct, total }),
    })
  )
}

export async function createFocusFlashcards(id: number): Promise<number> {
  const body = await parseOrThrow<{ created: number }>(
    await apiFetch(`/api/focus-areas/${id}/flashcards`, { method: 'POST' })
  )
  return body.created
}

function normalize(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .replace(/^[¡¿"'«]+|[.,;:!?"'»]+$/g, '')
}

function stripAccents(value: string): string {
  return value.normalize('NFD').replace(/[̀-ͯ]/g, '')
}

/**
 * Grade a drill answer. A right answer missing only its accents counts as
 * correct, flagged so the learner sees the spelling.
 */
export function gradeAnswer(
  input: string,
  item: Pick<DrillItem, 'answer' | 'accepted'>
): 'correct' | 'accents' | 'wrong' {
  const given = normalize(input)
  if (!given) return 'wrong'
  const options = [item.answer, ...item.accepted].map(normalize)
  if (options.includes(given)) return 'correct'
  if (options.map(stripAccents).includes(stripAccents(given))) return 'accents'
  return 'wrong'
}

/** Backend timestamps are naive UTC. */
export function parseServerDate(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)
}
