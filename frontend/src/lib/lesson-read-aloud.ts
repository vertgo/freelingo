/**
 * Turns a lesson's target-language content into the ordered parts that
 * "Play lesson" reads aloud. Native-language fields (native explanation,
 * translations, notes) are never included, so playback stays immersive.
 */

export interface ReadAloudPart {
  /** Highlight key shared by every chunk of the same block on the page. */
  key: string
  /** Text sent to TTS; long blocks are split into sentence chunks. */
  text: string
}

interface LessonReadAloudSource {
  title?: string | null
  explanation?: Record<string, unknown> | null
  vocabulary?: unknown
}

// Short chunks start playing sooner and stay well under the TTS text limit.
export const MAX_CHUNK_CHARS = 400

function clean(value: unknown): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : ''
}

/** Splits text at sentence ends, then packs sentences into short chunks. */
export function chunkText(text: string, maxChars = MAX_CHUNK_CHARS): string[] {
  const cleaned = clean(text)
  if (!cleaned) return []
  const sentences = cleaned.split(/(?<=[.!?…])\s+/)
  const chunks: string[] = []
  let current = ''
  for (const sentence of sentences) {
    if (current && current.length + 1 + sentence.length > maxChars) {
      chunks.push(current)
      current = sentence
    } else {
      current = current ? `${current} ${sentence}` : sentence
    }
  }
  if (current) chunks.push(current)
  return chunks
}

export function buildLessonReadAloud(
  lesson: LessonReadAloudSource
): ReadAloudPart[] {
  const parts: ReadAloudPart[] = []
  const add = (key: string, value: unknown) => {
    for (const text of chunkText(clean(value))) parts.push({ key, text })
  }

  add('title', lesson.title)
  const explanation = lesson.explanation ?? {}
  add('explanation', explanation.text)
  const keyPoints = Array.isArray(explanation.key_points)
    ? explanation.key_points
    : []
  keyPoints.forEach((point, i) => add(`key-point-${i}`, point))
  const examples = Array.isArray(explanation.examples)
    ? explanation.examples
    : []
  examples.forEach((example, i) =>
    add(`example-${i}`, (example as { sentence?: unknown })?.sentence)
  )
  const vocabulary = Array.isArray(lesson.vocabulary) ? lesson.vocabulary : []
  vocabulary.forEach((item, i) => {
    const entry = (item ?? {}) as Record<string, unknown>
    add(`vocab-${i}`, entry.word)
    add(`vocab-${i}`, entry.definition)
    add(`vocab-${i}`, entry.example)
  })
  return parts
}

/** Index of the first chunk of the block after the one at `index`. */
export function nextBlockStart(parts: ReadAloudPart[], index: number): number {
  if (index < 0) return 0
  const key = parts[index]?.key
  let i = index + 1
  while (i < parts.length && parts[i].key === key) i++
  return i
}

/** Index of the first chunk of the block before the one at `index`. */
export function previousBlockStart(
  parts: ReadAloudPart[],
  index: number
): number {
  const before = blockStart(parts, index) - 1
  return before < 0 ? 0 : blockStart(parts, before)
}

/** Index of the first chunk of the block containing `index`. */
export function blockStart(parts: ReadAloudPart[], index: number): number {
  if (index <= 0) return 0
  const key = parts[index]?.key
  let i = index
  while (i > 0 && parts[i - 1].key === key) i--
  return i
}
