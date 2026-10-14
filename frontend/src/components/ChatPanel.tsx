/**
 * The left full-height LLM panel.
 *
 * Two things the user can do here, and the panel keeps them apart:
 *
 * - **Ask about the run.** Routed to the chat agent, which answers as a
 *   grounded card (title / sentence / what the run shows / what to do / stats /
 *   confidence / caveats). The steps are shown while it works, and every number
 *   in the answer is checked against the run's own outputs.
 * - **Describe an intervention.** Still routed to the existing AI-plan
 *   pipeline, because that is a different job: it produces geometry, not an
 *   explanation.
 *
 * The card shape and the step/grounding events follow the Constellation repo's
 * agent contract -- the part worth copying is that the answer is a set of named
 * fields rather than prose, so a missing cause is visible instead of hidden in
 * a paragraph.
 *
 * Honesty: with no model configured the card is assembled by code and labelled
 * `template`. That is stated in the card itself, not just here.
 */
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage } from '@/api/client'
import {
  streamChat,
  type AnswerCard,
  type ChatTurn,
  type StreamEvent,
} from '@/api/chatApi'
import { createAiPlan } from '@/api/planApi'
import { buildReceipt, type Receipt } from '@/api/receipt'
import { useSceneStore } from '@/state/sceneStore'

interface Step {
  index: number
  title: string
  desc: string
  state: 'running' | 'done'
  ms?: number
}

interface Message {
  id: string
  role: 'ai' | 'user'
  text?: string
  card?: AnswerCard
  /** What the card rests on, snapshotted when the question was asked. */
  receipt?: Receipt | null
  chips?: string[]
}

const SUGGESTIONS = [
  'Is this better than the baseline?',
  'What should I do next?',
  'Add a retention basin north of the canal',
]

let nextId = 1
const mkId = () => `m${nextId++}`

/** Does this read as an intervention instruction rather than a question? */
function looksLikeIntervention(text: string): boolean {
  if (/\?$/.test(text.trim())) return false
  return /\b(add|place|plant|fill|remove|rotate|resize|raise|lower|move|scale|build)\b/i.test(text)
}

/**
 * Pull a string field out of a possibly-unfinished JSON document.
 *
 * The streamed draft is JSON, so it cannot be parsed until the closing brace.
 * Reading the field by regex is what lets the sentence appear as it is written
 * instead of all at once at the end.
 */
function partialField(raw: string, field: string): string | null {
  const m = new RegExp(`"${field}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`).exec(raw)
  if (!m) return null
  try {
    return JSON.parse(`"${m[1]}"`) as string
  } catch {
    return m[1]
  }
}

/**
 * "What is this based on?" -- collapsed by default, because most readers want the
 * answer first and the ones who want the receipt really want it. A native
 * <details> gives keyboard and screen-reader behaviour for free.
 */
function ReceiptView({ receipt }: { receipt: Receipt }) {
  return (
    <Box
      component="details"
      sx={{ mt: 1, '& summary': { cursor: 'pointer', listStyle: 'none' }, '& summary::-webkit-details-marker': { display: 'none' } }}
    >
      <Typography
        component="summary"
        className="hud-mono"
        sx={{ fontSize: 9, color: 'text.secondary', '&:hover': { color: 'text.primary' } }}
      >
        ▸ BASED ON · {receipt.summary.toUpperCase()}
      </Typography>
      <Box sx={{ mt: 0.5, pl: 1.25, borderLeft: '1px solid', borderColor: 'divider' }}>
        {receipt.lines.map((l) => (
          <Stack key={l.label} direction="row" spacing={1} alignItems="baseline">
            <Typography sx={{ fontSize: 10, color: 'text.secondary', minWidth: 96 }}>{l.label}</Typography>
            <Typography className="hud-mono" sx={{ fontSize: 10, wordBreak: 'break-word' }}>
              {l.value}
            </Typography>
          </Stack>
        ))}
      </Box>
    </Box>
  )
}

/** One answer card, rendered by field. */
function CardView({ card }: { card: AnswerCard }) {
  const ungrounded = card.grounding?.ungrounded ?? []
  return (
    <Box>
      {card.eyebrow && (
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', mb: 0.25 }}>
          {card.eyebrow.toUpperCase()}
        </Typography>
      )}
      <Typography sx={{ fontSize: 13.5, fontWeight: 600, lineHeight: 1.35, mb: 0.75 }}>
        {card.title}
      </Typography>
      <Typography sx={{ fontSize: 12.5, lineHeight: 1.6, color: 'text.primary' }}>
        {card.sentence}
      </Typography>

      {card.cause && (
        <Box sx={{ mt: 1.25 }}>
          <Typography className="hud-label">{card.l1}</Typography>
          <Typography sx={{ fontSize: 12, lineHeight: 1.55, mt: 0.25 }}>{card.cause}</Typography>
        </Box>
      )}
      {card.todo && (
        <Box sx={{ mt: 1 }}>
          <Typography className="hud-label">{card.l2}</Typography>
          <Typography sx={{ fontSize: 12, lineHeight: 1.55, mt: 0.25 }}>{card.todo}</Typography>
        </Box>
      )}

      {card.stats.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 1.25 }}>
          {card.stats.map((s) => (
            <Stack key={s.l} direction="row" justifyContent="space-between" alignItems="baseline" spacing={1}>
              <Typography sx={{ fontSize: 11, color: 'text.secondary' }}>{s.l}</Typography>
              <Box sx={{ textAlign: 'right' }}>
                <Typography className="hud-mono" sx={{ fontSize: 11.5, color: '#5cff9d' }}>
                  {s.v}
                </Typography>
                {s.ci && (
                  <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
                    {s.ci}
                  </Typography>
                )}
              </Box>
            </Stack>
          ))}
        </Stack>
      )}

      <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 1.25 }}>
        <Box
          className="hud-dot"
          sx={{
            color: card.confidence.level === 'High' ? '#5cff9d'
              : card.confidence.level === 'Medium' ? '#ffd54f' : '#ff9d2e',
          }}
        />
        <Typography sx={{ fontSize: 10.5 }}>
          {card.confidence.level} confidence · {card.confidence.pct}%
        </Typography>
      </Stack>
      <Typography sx={{ fontSize: 10.5, color: 'text.secondary', mt: 0.25, lineHeight: 1.5 }}>
        {card.confidence.note}
      </Typography>

      {ungrounded.length > 0 && (
        <Box sx={{ mt: 1, p: 0.75, borderRadius: '4px', border: '1px solid', borderColor: 'rgba(255,157,46,0.45)', bgcolor: 'rgba(255,157,46,0.08)' }}>
          <Typography sx={{ fontSize: 10.5, color: '#ff9d2e' }}>
            {card.grounding?.revised ? 'Revised, still ungrounded:' : 'Numbers not from this run:'}
          </Typography>
          {ungrounded.slice(0, 3).map((p) => (
            <Typography key={p} sx={{ fontSize: 10, color: 'text.secondary', mt: 0.25 }}>
              {p}
            </Typography>
          ))}
        </Box>
      )}

      {card.caveats.length > 0 && (
        <Box sx={{ mt: 1 }}>
          {card.caveats.map((c) => (
            <Typography key={c} sx={{ fontSize: 10, color: 'text.secondary', lineHeight: 1.5 }}>
              · {c}
            </Typography>
          ))}
        </Box>
      )}

      {card.source === 'template' && (
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'rgba(255,157,46,0.85)', mt: 0.75 }}>
          ASSEMBLED BY CODE · NO LANGUAGE MODEL CONFIGURED
        </Typography>
      )}
    </Box>
  )
}

/** The progress stepper: one row per step, ticked as it finishes. */
function StepperView({ steps }: { steps: Step[] }) {
  return (
    <Stack spacing={0.5}>
      {steps.map((s) => (
        <Stack key={s.index} direction="row" alignItems="center" spacing={0.75}>
          <Box
            sx={{
              width: 10,
              textAlign: 'center',
              fontSize: 11,
              color: s.state === 'done' ? '#5cff9d' : 'rgba(125,146,138,0.7)',
            }}
          >
            {s.state === 'done' ? '✓' : '·'}
          </Box>
          <Typography
            className={s.state === 'running' ? 'hud-shimmer' : undefined}
            sx={{
              fontSize: 11,
              color: s.state === 'done' ? 'text.primary' : 'text.secondary',
            }}
          >
            {s.title}
          </Typography>
          {s.state === 'done' && s.ms !== undefined && (
            <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
              {s.ms} ms
            </Typography>
          )}
        </Stack>
      ))}
    </Stack>
  )
}

export default function ChatPanel() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const runResult = useSceneStore((s) => s.runResult)
  const running = useSceneStore((s) => s.running)

  const [messages, setMessages] = useState<Message[]>([
    {
      id: mkId(),
      role: 'ai',
      text: 'Sandbox loaded. Adjust the rainfall and temperature sliders (top-right), toggle a project, then press Run. After the run I can answer questions about what changed.',
      chips: SUGGESTIONS,
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<Step[]>([])
  // What a screen reader is told. The thread itself is not a live region: it would
  // re-announce every card on each render. This one line carries only what is new.
  const [announcement, setAnnouncement] = useState('')
  const [draft, setDraft] = useState<{ title: string | null; sentence: string | null } | null>(null)
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null)
  const lastRunId = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // The model-configured dot. Green = live LLM, amber = parser only, grey = unknown.
  useEffect(() => {
    let cancelled = false
    void fetch('/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`status ${r.status}`))))
      .then((d) => { if (!cancelled) setLlmConfigured(Boolean(d?.llm?.configured)) })
      .catch(() => { if (!cancelled) setLlmConfigured(null) })
    return () => { cancelled = true }
  }, [])

  // After each run, push the explanation as an AI message (once per runId).
  useEffect(() => {
    if (!runResult || running) return
    if (lastRunId.current === runResult.runId) return
    lastRunId.current = runResult.runId
    setMessages((prev) => [
      ...prev,
      {
        id: mkId(),
        role: 'ai',
        text: runResult.explanation +
          (runResult.explanationSource === 'template'
            // The backend names the cause; the client does not guess. "No model
            // configured" and "the model was rate-limited" are different
            // problems and only one of them is worth waiting out.
            ? `\n\n_Assembled by code from the run's own numbers${
                runResult.explanationNote ? ` — ${runResult.explanationNote}` : ''
              }._`
            : ''),
        chips: SUGGESTIONS,
      },
    ])
  }, [runResult, running])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, steps, draft])

  /** Ask the chat agent. Streams, so the steps and the sentence arrive as they happen. */
  const ask = useCallback(async (question: string, history: ChatTurn[]) => {
    if (!sceneId) return
    const controller = new AbortController()
    abortRef.current = controller
    setSteps([])
    setDraft(null)

    // Snapshot now: if the planner runs again while this streams, the card must
    // still name the run it was actually asked about.
    const receipt = buildReceipt(runResult)

    let raw = ''
    const onEvent = (event: StreamEvent) => {
      switch (event.event) {
        case 'step_started':
          setAnnouncement(`${event.title}…`)
          setSteps((prev) => [
            ...prev.filter((s) => s.index !== event.index),
            { index: event.index, title: event.title, desc: event.desc, state: 'running' },
          ])
          break
        case 'step_finished':
          setSteps((prev) =>
            prev.map((s) => (s.index === event.index ? { ...s, state: 'done', ms: event.ms } : s)),
          )
          break
        case 'delta':
          // The draft is JSON, so fields are read out of the partial document
          // rather than parsed -- that is what makes it appear mid-stream.
          raw += event.text
          setDraft({
            title: partialField(raw, 'title'),
            sentence: partialField(raw, 'sentence'),
          })
          break
        case 'answer':
          setAnnouncement(`${event.answer.title}. ${event.answer.sentence}`)
          setMessages((prev) => [
            ...prev,
            {
              id: mkId(),
              role: 'ai',
              card: event.answer,
              // A "general" answer did not use the run's data, so it carries no receipt.
              receipt: event.answer.kind === 'run' ? receipt : null,
              chips: event.answer.followups,
            },
          ])
          break
        case 'error':
          setAnnouncement(`Note: ${event.message}`)
          setMessages((prev) => [
            ...prev,
            { id: mkId(), role: 'ai', text: `Note: ${event.message}` },
          ])
          break
        default:
          break
      }
    }

    try {
      await streamChat(sceneId, { question, run: runResult, history }, onEvent, controller.signal)
    } catch (err) {
      if (!controller.signal.aborted) {
        setMessages((prev) => [...prev, { id: mkId(), role: 'ai', text: `Error: ${errorMessage(err)}` }])
      }
    } finally {
      abortRef.current = null
      setSteps([])
      setDraft(null)
      setBusy(false)
    }
  }, [sceneId, runResult])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || busy || !sceneId) return
    setMessages((prev) => [...prev, { id: mkId(), role: 'user', text: trimmed }])
    setInput('')
    setBusy(true)

    // Interventions are geometry, not explanation: they keep the AI-plan route.
    if (looksLikeIntervention(trimmed)) {
      try {
        const r = await createAiPlan(sceneId, trimmed)
        if (r.features > 0 && r.planId) {
          const { usePlanStore } = await import('@/state/planStore')
          await usePlanStore.getState().refreshPlans(sceneId)
          await usePlanStore.getState().selectPlan(r.planId)
          setMessages((prev) => [
            ...prev,
            {
              id: mkId(),
              role: 'ai',
              text: `Placed ${r.features} intervention${r.features === 1 ? '' : 's'} (${r.types.join(', ') || 'geometry'}) into the sandbox. Status: draft. Run again to see the effect.`,
              chips: SUGGESTIONS,
            },
          ])
        } else {
          setMessages((prev) => [
            ...prev,
            { id: mkId(), role: 'ai', text: r.message || 'I couldn’t place that as an intervention. Try naming a type (retention basin, trees, green space, water reservoir) and a location.' },
          ])
        }
      } catch (err) {
        setMessages((prev) => [...prev, { id: mkId(), role: 'ai', text: `Error: ${errorMessage(err)}` }])
      } finally {
        setBusy(false)
      }
      return
    }

    const history: ChatTurn[] = messages
      .filter((m): m is Message & { text: string } => Boolean(m.text))
      .slice(-6)
      .map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', text: m.text }))
    await ask(trimmed, history)
  }

  const dotColor = llmConfigured === null ? '#7d928a' : llmConfigured ? '#5cff9d' : '#ff9d2e'

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 0,
        left: 0,
        bottom: 0,
        width: 340,
        zIndex: 1100,
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(6, 10, 9, 0.92)',
        backdropFilter: 'blur(16px)',
      }}
    >
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.5, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Box className="hud-dot" sx={{ color: dotColor }} />
        <Box sx={{ flex: 1 }}>
          <Typography className="hud-label">Assistant</Typography>
          <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
            {llmConfigured === null
              ? 'LLM status unknown'
              : llmConfigured
                ? 'Live model'
                : 'Heuristic parser — not AI'}
          </Typography>
        </Box>
        {busy && (
          <IconButton
            aria-label="Stop"
            size="small"
            onClick={() => abortRef.current?.abort()}
            sx={{ width: 24, height: 24, color: 'text.secondary' }}
          >
            <span style={{ fontSize: 11 }}>■</span>
          </IconButton>
        )}
      </Stack>

      {/* Announces progress and the finished answer to assistive tech. Visually
          hidden rather than display:none, which would remove it from the tree. */}
      <Box
        role="status"
        aria-live="polite"
        aria-atomic="true"
        sx={{
          position: 'absolute',
          width: 1,
          height: 1,
          overflow: 'hidden',
          clip: 'rect(0 0 0 0)',
          whiteSpace: 'nowrap',
        }}
      >
        {announcement}
      </Box>

      {/* Thread */}
      <Box ref={scrollRef} sx={{ flex: 1, overflowY: 'auto', px: 2, py: 2 }}>
        <Stack spacing={1.75}>
          {messages.map((m) => (
            <Box
              key={m.id}
              sx={{
                alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '92%',
                px: 1.5,
                py: 1.25,
                borderRadius: m.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                border: '1px solid',
                borderColor: m.role === 'user' ? 'rgba(92,255,157,0.4)' : 'divider',
                bgcolor: m.role === 'user' ? 'rgba(92,255,157,0.08)' : 'rgba(255,255,255,0.03)',
              }}
            >
              {m.card ? (
                <>
                  <CardView card={m.card} />
                  {m.receipt && <ReceiptView receipt={m.receipt} />}
                </>
              ) : (
                <Typography
                  variant="body2"
                  sx={{ fontSize: 12.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', color: 'text.primary' }}
                >
                  {m.text}
                </Typography>
              )}
              {m.chips && (
                <Stack direction="row" spacing={0.5} sx={{ mt: 1, flexWrap: 'wrap', gap: 0.5 }}>
                  {m.chips.map((c) => (
                    <Chip key={c} label={c} size="small" onClick={() => void send(c)} sx={{ cursor: 'pointer' }} />
                  ))}
                </Stack>
              )}
            </Box>
          ))}

          {/* Live progress while the agent works */}
          {busy && (
            <Box
              sx={{
                alignSelf: 'flex-start',
                maxWidth: '92%',
                px: 1.5,
                py: 1.25,
                borderRadius: '12px 12px 12px 2px',
                border: '1px solid',
                borderColor: 'divider',
                bgcolor: 'rgba(255,255,255,0.03)',
              }}
            >
              {steps.length > 0 && <StepperView steps={steps} />}
              {draft?.title && (
                <Typography sx={{ fontSize: 13, fontWeight: 600, mt: 1, lineHeight: 1.35 }}>
                  {draft.title}
                </Typography>
              )}
              {draft?.sentence && (
                <Typography sx={{ fontSize: 12.5, lineHeight: 1.6, mt: 0.5 }}>
                  {draft.sentence}
                  <Box component="span" sx={{ opacity: 0.5 }}>▌</Box>
                </Typography>
              )}
              {!draft?.sentence && steps.length === 0 && (
                <Typography className="hud-shimmer" variant="caption">working…</Typography>
              )}
            </Box>
          )}
        </Stack>
      </Box>

      {/* Input */}
      <Box sx={{ p: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
        <Stack direction="row" alignItems="flex-end" spacing={1}>
          <TextField
            fullWidth
            multiline
            maxRows={3}
            placeholder="Ask about the run, or describe an intervention…"
            value={input}
            disabled={busy || !sceneId}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send(input)
              }
            }}
            sx={{ '& .MuiOutlinedInput-root': { fontSize: 12.5 } }}
          />
          <IconButton
            aria-label="Send"
            disabled={busy || !input.trim() || !sceneId}
            onClick={() => void send(input)}
            sx={{
              width: 32,
              height: 32,
              borderRadius: '8px',
              bgcolor: 'rgba(92,255,157,0.14)',
              border: '1px solid rgba(92,255,157,0.4)',
              color: '#5cff9d',
              '&:hover': { bgcolor: 'rgba(92,255,157,0.28)' },
              '&.Mui-disabled': { borderColor: 'divider', color: 'rgba(125,146,138,0.4)' },
            }}
          >
            <span style={{ fontSize: 14 }}>↑</span>
          </IconButton>
        </Stack>
        <Typography className="hud-mono" sx={{ fontSize: 8.5, color: 'rgba(125,146,138,0.6)', mt: 0.5 }}>
          ENTER TO SEND · SHIFT+ENTER FOR A NEW LINE
        </Typography>
      </Box>
    </Box>
  )
}
