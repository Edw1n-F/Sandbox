/**
 * `streamChat` against real ReadableStreams.
 *
 * The case that matters is a body that closes without its terminal `done`: the
 * server promises exactly one, so its absence means the connection was cut, and
 * the panel must be told rather than left looking busy. The other cases pin that
 * the guard does not fire when it should not (a normal stream, an abort), and that
 * the SSE framing survives a chunk boundary falling mid-frame.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamChat, type StreamEvent } from './chatApi'

const enc = new TextEncoder()

/** An SSE frame for an event, as the server writes it. */
const frame = (e: StreamEvent) => `data: ${JSON.stringify(e)}\n\n`

/** A fetch that answers with `chunks` as the response body, then closes it. */
function mockFetch(chunks: string[], { status = 200 }: { status?: number } = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const c of chunks) controller.enqueue(enc.encode(c))
          controller.close()
        },
      })
      return { ok: status === 200, status, body } as unknown as Response
    }),
  )
}

async function collect(signal?: AbortSignal): Promise<StreamEvent[]> {
  const seen: StreamEvent[] = []
  await streamChat('s1', { question: 'q' }, (e) => seen.push(e), signal)
  return seen
}

const names = (events: StreamEvent[]) => events.map((e) => e.event)

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChat', () => {
  it('passes a complete stream through untouched, with no synthetic events', async () => {
    mockFetch([
      frame({ event: 'run_started', run_id: 'r1' }),
      frame({ event: 'done', status: 'done', ms: 5 }),
    ])
    expect(names(await collect())).toEqual(['run_started', 'done'])
  })

  it('reports a stream that closes without done, then ends it', async () => {
    mockFetch([
      frame({ event: 'run_started', run_id: 'r1' }),
      frame({ event: 'step_started', index: 0, title: 't', desc: 'd', tool: 'facts' }),
    ])
    const events = await collect()
    expect(names(events)).toEqual(['run_started', 'step_started', 'error', 'done'])

    const err = events[2]
    expect(err.event === 'error' && err.recoverable).toBe(true)
    const done = events[3]
    expect(done.event === 'done' && done.status).toBe('failed')
  })

  it('reports an empty body, which is also a cut stream', async () => {
    mockFetch([])
    expect(names(await collect())).toEqual(['error', 'done'])
  })

  it('does not double up when the server already sent an error and a done', async () => {
    mockFetch([
      frame({ event: 'error', message: 'no model', recoverable: true }),
      frame({ event: 'done', status: 'failed', ms: 1 }),
    ])
    expect(names(await collect())).toEqual(['error', 'done'])
  })

  it('reads a frame that a chunk boundary splits in half', async () => {
    const whole = frame({ event: 'delta', text: 'hello' })
    const cut = Math.floor(whole.length / 2)
    mockFetch([
      whole.slice(0, cut),
      whole.slice(cut),
      frame({ event: 'done', status: 'done', ms: 1 }),
    ])
    const events = await collect()
    expect(names(events)).toEqual(['delta', 'done'])
    expect(events[0].event === 'delta' && events[0].text).toBe('hello')
  })

  it('skips a malformed frame instead of killing the stream', async () => {
    mockFetch([
      'data: {not json\n\n',
      frame({ event: 'done', status: 'done', ms: 1 }),
    ])
    expect(names(await collect())).toEqual(['done'])
  })

  it('does not report an abort as a dropped connection', async () => {
    const controller = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const body = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(enc.encode(frame({ event: 'run_started', run_id: 'r1' })))
            // Never closes: the caller aborts instead.
            controller.signal.addEventListener('abort', () =>
              c.error(new DOMException('aborted', 'AbortError')),
            )
          },
        })
        return { ok: true, status: 200, body } as unknown as Response
      }),
    )

    const seen: StreamEvent[] = []
    const pending = streamChat('s1', { question: 'q' }, (e) => seen.push(e), controller.signal)
    await Promise.resolve()
    controller.abort()

    await expect(pending).rejects.toThrow()
    expect(names(seen)).not.toContain('error')
    expect(names(seen)).not.toContain('done')
  })

  it('throws on an HTTP failure, before any stream exists', async () => {
    mockFetch([], { status: 503 })
    await expect(collect()).rejects.toThrow(/503/)
  })
})
