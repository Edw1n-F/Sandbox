/**
 * Chat endpoints: one question about a run, answered as a grounded card.
 *
 * One way to ask:
 *
 * - `streamChat`   -- server-sent events: steps, deltas, the card.
 *
 * The event names mirror the backend's `app/schemas/stream.py` set (itself
 * taken from the Constellation repo): `run_started`, `step_started`,
 * `step_finished`, `delta`, `grounding`, `answer`, `error`, `done`.
 *
 * SSE is read from `fetch` rather than `EventSource` because the request is a
 * POST with a body -- `EventSource` can only GET, and the run this question is
 * about has to be sent.
 */

/** One headline number. */
export interface StatItem {
  l: string
  v: string
  ci?: string | null
}

export interface Confidence {
  level: 'High' | 'Medium' | 'Low'
  pct: number
  note: string
}

/** The outcome of the number check. Surfaced, not hidden. */
export interface GroundingReport {
  ok: boolean
  ungrounded: string[]
  revised: boolean
}

/**
 * The answer card.
 *
 * Same field names as the backend's `AnswerCard`, so it is rendered by field
 * rather than parsed out of prose. `source` says whether a model wrote it or
 * code assembled it, which the panel shows rather than leaving the reader to
 * assume.
 */
export interface AnswerCard {
  kind: 'run' | 'general'
  title: string
  eyebrow?: string | null
  sentence: string
  l1: string
  cause?: string | null
  l2: string
  todo?: string | null
  stats: StatItem[]
  /**
   * Present on the full run card; absent (null) on the slim chat card, which
   * answers with a headline and a sentence only.
   */
  confidence?: Confidence | null
  caveats: string[]
  followups: string[]
  source: 'model' | 'template'
  grounding?: GroundingReport | null
  /**
   * Plan v2 §4: what the model decided the user wanted. Optional so a card
   * from a backend that predates this field (parser/template) is still
   * typeable -- the ChatPanel falls back to `looksLikeIntervention` when
   * the field is absent.
   */
  intent?: 'question' | 'intervention' | 'ambiguous'
  /**
   * The normalised intervention instruction, only present when intent='intervention'.
   * `intent_hint` on the wire -- AnswerCard does not go through the camelCase
   * alias generator the WireModel subclasses use, so the snake_case name is
   * what the backend emits.
   */
  intent_hint?: string | null
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
}

export interface ChatRequestBody {
  question: string
  run?: unknown | null
  history?: ChatTurn[]
}

// ---- streaming ---------------------------------------------------------- //

export type StreamEvent =
  | { event: 'run_started'; run_id: string }
  | { event: 'step_started'; index: number; title: string; desc: string; tool: string }
  | {
      event: 'step_finished'
      index: number
      title: string
      desc: string
      tool: string
      ms: number
      result?: string | null
    }
  | { event: 'delta'; text: string }
  | { event: 'grounding'; ok: boolean; ungrounded: string[]; revised: boolean }
  | { event: 'answer'; answer: AnswerCard }
  | { event: 'error'; message: string; recoverable: boolean }
  | { event: 'done'; status: string; ms: number }

/**
 * `POST /api/scenes/{sceneId}/chat/stream` -- the same card, arriving as events.
 *
 * Calls `onEvent` for each one and resolves on `done`. The stream always ends
 * with exactly one `done`, even on failure, so the caller can always stop its
 * spinner in one place.
 */
export async function streamChat(
  sceneId: string,
  body: ChatRequestBody,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/scenes/${sceneId}/chat/stream`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`chat stream failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false

  // SSE frames are separated by a blank line; a chunk boundary can fall
  // anywhere, so the tail is kept until the next chunk completes it.
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let split = buffer.indexOf('\n\n')
    while (split !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      for (const line of frame.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          const event = JSON.parse(line.slice(5).trim()) as StreamEvent
          if (event.event === 'done') sawDone = true
          onEvent(event)
        } catch {
          // A malformed frame is skipped rather than killing the stream.
        }
      }
      split = buffer.indexOf('\n\n')
    }
  }

  // The server ends every stream with exactly one `done`. A body that closes
  // without it was cut short (a proxy timeout, a dropped connection, a crashed
  // worker), and without this the panel would keep its steps ticking as if the
  // answer were still coming. An abort never reaches here: it throws from `read`.
  if (!sawDone) {
    onEvent({
      event: 'error',
      message: 'The connection closed before the answer finished. Ask again to retry.',
      recoverable: true,
    })
    onEvent({ event: 'done', status: 'failed', ms: 0 })
  }
}
