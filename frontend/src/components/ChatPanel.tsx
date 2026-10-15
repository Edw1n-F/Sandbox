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
import Button from '@mui/material/Button'
import Checkbox from '@mui/material/Checkbox'
import Chip from '@mui/material/Chip'
import FormControlLabel from '@mui/material/FormControlLabel'
import IconButton from '@mui/material/IconButton'
import Popover from '@mui/material/Popover'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'
import LLMConfigPanel from './LLMConfigPanel'

import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage } from '@/api/client'
import {
  streamChat,
  type AnswerCard,
  type ChatTurn,
  type StreamEvent,
} from '@/api/chatApi'
import { applyAiPlan, createAiPlan, type AiPlanResult, type InterventionProposal } from '@/api/planApi'
import { INTENT_VOCABULARY, looksLikeIntervention } from '@/api/intentVocabulary'
import { buildReceipt, type Receipt } from '@/api/receipt'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

interface Step {
  index: number
  title: string
  desc: string
  state: 'running' | 'done'
  ms?: number
}

interface ProposalPayload {
  instruction: string
  provider: string
  proposals: InterventionProposal[]
  /**
   * Refusals from the constraint engine (DESIGN §2.2). They travel with the
   * payload, not as an error: a refused placement is shown, never dropped.
   * Typed through `AiPlanResult` so it cannot drift from the wire contract.
   */
  rejected: NonNullable<AiPlanResult['rejected']>
  groundingNotes: string[]
  candidateCounts: Record<string, number>
  resolved?: 'applied' | 'dismissed'
}

interface Message {
  id: string
  role: 'ai' | 'user'
  text?: string
  card?: AnswerCard
  /** What the card rests on, snapshotted when the question was asked. */
  receipt?: Receipt | null
  chips?: string[]
  /** Confirm gate for ai-plan proposals (plan v2 §5). */
  proposals?: ProposalPayload
}

/**
 * Fallback chips (plan v2 §7): intervention instruction sentences, so clicking
 * one goes through the ai-plan confirm gate rather than looping as another chat
 * question. Only used when the backend did not supply its own followups.
 */
const SUGGESTIONS = [
  'add a water reservoir in the north',
  'add green space in the centre',
  'add permeable paving across the whole area',
]

let nextId = 1
const mkId = () => `m${nextId++}`

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
/**
 * Why this card was assembled by code, in the card's own words.
 *
 * The backend writes the reason into the template's confidence note and its
 * first caveat (both from `run_card.py`), so it is read from there rather than
 * re-derived here. The badge used to hardcode "no language model configured",
 * which was false whenever a model WAS configured and had merely been
 * rate-limited -- the normal state of a free tier.
 */
function reasonOf(card: AnswerCard): string {
  const caveat = card.caveats[0] ?? ''
  if (caveat.startsWith('Assembled by code: ')) {
    return caveat.slice('Assembled by code: '.length).replace(/\.$/, '')
  }
  const note = card.confidence?.note ?? ''
  const at = note.lastIndexOf('; ')
  return at >= 0 ? note.slice(at + 2).replace(/\.$/, '') : 'assembled from the run'
}

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
          {/* The badge names the real cause rather than asserting one. A
              429 and a missing key look identical from the source alone, and
              only one is worth retrying. */}
          ASSEMBLED BY CODE · {reasonOf(card).toUpperCase()}
        </Typography>
      )}
    </Box>
  )
}

/**
 * The size / constraints / parameters of one proposal, read by field (§2.2).
 *
 * A proposal is presented with its evidence and its limits, not as a bare type:
 * the size it actually covers, whether it touches a building or an already-staged
 * feature, how much the server clipped away, whether it is inside the caps, and
 * what the ledger will make of it. The warning line is always shown, so a
 * screening estimate never reads as a design.
 */
function ProposalDetail({ proposal }: { proposal: InterventionProposal }) {
  const size = proposal.size
  const constraints = proposal.constraints
  const parameters = proposal.parameters ? Object.entries(proposal.parameters) : []
  return (
    <Box sx={{ mt: 0.25 }}>
      {size && (
        <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
          size: {size.cells} cells · {size.areaHa} ha · {(size.coverageFraction * 100).toFixed(2)}% of area
        </Typography>
      )}
      {constraints && (
        <Box sx={{ mt: 0.25 }}>
          <Typography
            className="hud-mono"
            sx={{ fontSize: 9.5, color: constraints.buildingsClear ? '#5cff9d' : '#ff6b6b', lineHeight: 1.4 }}
          >
            buildings: {constraints.buildingsClear ? 'clear' : `overlaps ${constraints.buildingOverlapCells} cell(s)`}
            {constraints.buildingIds.length > 0 ? ` (${constraints.buildingIds.join(', ')})` : ''}
          </Typography>
          <Typography
            className="hud-mono"
            sx={{ fontSize: 9.5, color: constraints.overlapCells === 0 ? 'text.secondary' : '#ff9d2e', lineHeight: 1.4 }}
          >
            overlap: {constraints.overlapCells} cell(s)
            {constraints.overlapsWith.length > 0 ? ` with ${constraints.overlapsWith.join(', ')}` : ''}
          </Typography>
          {constraints.clippedCells > 0 && (
            <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
              clipped: {constraints.clippedCells} cell(s) ({(constraints.clippedFraction * 100).toFixed(1)}%)
            </Typography>
          )}
          <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
            caps: single {constraints.withinSingleCap ? 'ok' : 'over'} · cumulative {constraints.withinCumulativeCap ? 'ok' : 'over'}
          </Typography>
          <Typography sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.45, mt: 0.25 }}>
            effect: {constraints.effect}{constraints.effectNote ? ` — ${constraints.effectNote}` : ''}
          </Typography>
        </Box>
      )}
      {parameters.map(([name, entry]) => (
        <Typography key={name} className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
          {name}: {entry.value} {entry.unit} · {entry.status} · {entry.source}
        </Typography>
      ))}
      {proposal.status && (
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.4, mt: 0.25 }}>
          status: {proposal.status}
        </Typography>
      )}
      {proposal.warning && (
        <Typography sx={{ fontSize: 9.5, color: 'rgba(255,157,46,0.9)', lineHeight: 1.45 }}>
          {proposal.warning}
        </Typography>
      )}
    </Box>
  )
}

/**
 * The proposals the constraint engine refused, with the reason named (§2.2, D5).
 *
 * A refusal is a first-class object, not an error: a silently shorter list is
 * indistinguishable from a model that returned less, so a refused placement is
 * always visible with its `reasonCode` and its plain-words `warning`.
 */
function RefusalList({ rejected }: { rejected: NonNullable<AiPlanResult['rejected']> }) {
  if (rejected.length === 0) return null
  return (
    <Box
      sx={{
        mt: 0.75,
        p: 0.75,
        borderRadius: '4px',
        border: '1px solid',
        borderColor: 'rgba(255,107,107,0.4)',
        bgcolor: 'rgba(255,107,107,0.07)',
      }}
    >
      <Typography className="hud-label" sx={{ fontSize: 9, color: '#ff6b6b' }}>
        NOT PLACED ({rejected.length})
      </Typography>
      {rejected.map((r, i) => (
        <Box key={`${r.siteId ?? r.interventionType}-${i}`} sx={{ mt: 0.5 }}>
          <Typography sx={{ fontSize: 10.5, lineHeight: 1.35 }}>
            {r.interventionType}
            {r.siteId ? ` · ${r.siteId}` : ''} — {r.reasonCode}
          </Typography>
          {r.warning && (
            <Typography sx={{ fontSize: 10, color: 'text.secondary', lineHeight: 1.4 }}>
              {r.warning}
            </Typography>
          )}
        </Box>
      ))}
    </Box>
  )
}

/**
 * Confirm gate panel (plan v2 §5): a checklist of proposals + Apply/Dismiss.
 *
 * The payload sits on the message. Once resolved the message is marked and the
 * checklist swapped for a status line, so re-rendering the chat does not
 * resurrect the controls -- a proposal state only exists in the user's head
 * once they have chosen.
 */
function ProposalReview({
  payload,
  sceneId,
  onResolved,
}: {
  payload: ProposalPayload
  sceneId: string
  onResolved: (outcome: 'applied' | 'dismissed', note?: string) => void
}) {
  const [checked, setChecked] = useState<boolean[]>(() =>
    payload.proposals.map(() => true),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (payload.resolved === 'applied') {
    return (
      <Typography
        variant="caption"
        sx={{ display: 'block', mt: 1, color: 'text.secondary' }}
      >
        Applied. Run again to see the effect.
      </Typography>
    )
  }
  if (payload.resolved === 'dismissed') {
    return (
      <Typography
        variant="caption"
        sx={{ display: 'block', mt: 1, color: 'text.secondary' }}
      >
        Dismissed. Nothing was persisted.
      </Typography>
    )
  }

  const anyChecked = checked.some(Boolean)

  const apply = async () => {
    const selected = payload.proposals.filter((_, i) => checked[i])
    if (selected.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const r = await applyAiPlan(sceneId, {
        instruction: payload.instruction,
        provider: payload.provider,
        proposals: selected,
        groundingNotes: payload.groundingNotes,
        candidateCounts: payload.candidateCounts,
      })
      if (r.planId) {
        // The draft is now a persisted plan: drop the preview so the viewer
        // swaps the dashed draft blocks for the real ones (DESIGN §2.4).
        usePlanStore.getState().clearProposals()
        await usePlanStore.getState().refreshPlans(sceneId)
        await usePlanStore.getState().selectPlan(r.planId)
        onResolved(
          'applied',
          `Applied ${r.features} intervention${r.features === 1 ? '' : 's'} (${(r.types ?? []).join(', ') || 'geometry'}). Status: draft. Run again to see the effect.`,
        )
      } else {
        setError(r.message ?? 'The server returned no plan id.')
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={0.5} sx={{ mt: 1 }}>
      {payload.proposals.map((p, i) => (
        <FormControlLabel
          key={`${p.siteId ?? p.interventionType}-${i}`}
          control={
            <Checkbox
              size="small"
              checked={checked[i]}
              onChange={(_, v) =>
                setChecked((prev) => prev.map((old, idx) => (idx === i ? v : old)))
              }
              sx={{ py: 0.25 }}
            />
          }
          label={
            <Box>
              <Typography sx={{ fontSize: 11.5, lineHeight: 1.3 }}>
                {p.interventionType}
                {p.siteId ? ` · ${p.siteId}` : ''}
              </Typography>
              {p.rationale && (
                <Typography
                  sx={{ fontSize: 10.5, color: 'text.secondary', lineHeight: 1.3 }}
                >
                  {p.rationale}
                </Typography>
              )}
              {p.basis && (
                <Typography
                  className="hud-mono"
                  sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.3 }}
                >
                  basis: {p.basis}
                </Typography>
              )}
              <ProposalDetail proposal={p} />
            </Box>
          }
          sx={{ alignItems: 'flex-start', ml: 0, mr: 0 }}
        />
      ))}
      <RefusalList rejected={payload.rejected} />
      {error && (
        <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>
      )}
      <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
        <Button
          size="small"
          variant="contained"
          disabled={!anyChecked || busy}
          onClick={() => void apply()}
          sx={{ fontSize: 11 }}
        >
          {busy ? 'Applying…' : `Apply ${checked.filter(Boolean).length}`}
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          onClick={() => {
            // A dismissed draft must disappear from the scene (DESIGN §2.4).
            usePlanStore.getState().clearProposals()
            onResolved('dismissed')
          }}
          sx={{ fontSize: 11 }}
        >
          Dismiss
        </Button>
      </Stack>
    </Stack>
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
  /** Plan v2 §8 whitelist: empty = no restriction, keys match ai_plan.POOL_FOR_KIND. */
  const [allowedTypes, setAllowedTypes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [steps, setSteps] = useState<Step[]>([])
  // What a screen reader is told. The thread itself is not a live region: it would
  // re-announce every card on each render. This one line carries only what is new.
  const [announcement, setAnnouncement] = useState('')
  const [draft, setDraft] = useState<{ title: string | null; sentence: string | null } | null>(null)
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null)
  const [configAnchor, setConfigAnchor] = useState<HTMLElement | null>(null)
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

    // The run always carries a card -- written by the model when one answered,
    // by code otherwise -- so a run renders as the same structured card the
    // chat produces. The paragraph is the fallback for a client that predates
    // this, or for a response missing the field.
    const runCard = runResult.explanationCard as AnswerCard | undefined
    // Prefer the card's own followups (plan v2 §7: the backend already shapes
    // these as intervention instructions). Fall back to the constant for a
    // response that predates the followup contract.
    const runChips = runCard?.followups?.length ? runCard.followups : SUGGESTIONS
    setMessages((prev) => [
      ...prev,
      runCard
        ? {
            id: mkId(),
            role: 'ai' as const,
            card: runCard,
            receipt: buildReceipt(runResult),
            chips: runChips,
          }
        : {
            id: mkId(),
            role: 'ai' as const,
            text: runResult.explanation,
            receipt: buildReceipt(runResult),
            chips: runChips,
          },
    ])
  }, [runResult, running])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, steps, draft])

  /**
   * Call ai-plan in propose mode and append a confirm-gate message. Shared by
   * two entry points (plan v2 §4 intent auto-route and the legacy
   * `looksLikeIntervention` fast path) so the behaviours cannot drift.
   *
   * `source` says which path brought us here, used only for the chat log line.
   */
  const openProposalGate = useCallback(
    async (instruction: string, source: 'regex' | 'intent') => {
      if (!sceneId) return
      try {
        const r: AiPlanResult = await createAiPlan(sceneId, instruction, {
          apply: false,
          allowedTypes: allowedTypes.length > 0 ? allowedTypes : undefined,
        })
        const proposals = r.proposals ?? []
        const rejected = r.rejected ?? []
        if (proposals.length === 0 && rejected.length === 0) {
          setMessages((prev) => [
            ...prev,
            {
              id: mkId(),
              role: 'ai',
              text:
                r.message ||
                'I couldn’t propose anything for that instruction. Try naming a type (retention basin, trees, green space, water reservoir) and a location.',
            },
          ])
          return
        }
        // Hold the draft in the store so the viewer can draw the translucent,
        // dashed preview blocks (DESIGN §2.4). It is cleared on Apply/Dismiss.
        usePlanStore.getState().setProposals(proposals)
        const prefix =
          source === 'intent'
            ? `Routed to the design loop: ${instruction}. Proposed ${proposals.length} intervention${proposals.length === 1 ? '' : 's'} via ${r.provider}.`
            : `Proposed ${proposals.length} intervention${proposals.length === 1 ? '' : 's'} via ${r.provider}. Review and apply the ones to keep.`
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'ai',
            text: prefix,
            proposals: {
              instruction,
              provider: r.provider,
              proposals,
              rejected,
              groundingNotes: r.groundingNotes ?? [],
              candidateCounts: r.candidateCounts ?? {},
            },
          },
        ])
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          { id: mkId(), role: 'ai', text: `Error: ${errorMessage(err)}` },
        ])
      }
    },
    [sceneId, allowedTypes],
  )

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
        case 'answer': {
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
          // Plan v2 §4 intent auto-route: when the LLM classified the user's
          // turn as an intervention, open the ai-plan confirm gate without a
          // second round trip. The card itself still renders -- the hint goes
          // *in addition*, so the user sees why the gate opened.
          if (event.answer.intent === 'intervention' && event.answer.intent_hint) {
            void openProposalGate(event.answer.intent_hint, 'intent')
          }
          break
        }
        case 'error':
          // A provider error is not content. The card that follows already says
          // what happened and which data it rests on, so printing the raw
          // failure as a chat message put "Note: provider HTTP 429" in the
          // thread next to a perfectly good answer. The live region still
          // announces it, which is where a screen reader should hear it.
          setAnnouncement(`Note: ${event.message} — the answer may be code-assembled.`)
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
  }, [sceneId, runResult, openProposalGate])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || busy || !sceneId) return
    setMessages((prev) => [...prev, { id: mkId(), role: 'user', text: trimmed }])
    setInput('')
    setBusy(true)

    // Plan v2 §4 / DESIGN §2.5: two-track routing.
    //   1. If the input obviously reads as an intervention, skip the chat round
    //      trip and open the confirm gate directly. The fast path reads the
    //      shared vocabulary (`INTENT_VOCABULARY`, pinned to the server's), so it
    //      no longer silently drops `dig`/`create`/`grow` (cause R2). Doing a
    //      full SSE first costs 2-5 s of latency the user feels.
    //   2. Otherwise ask the agent. The server runs the same classifier on the
    //      card, so `intent="intervention"` now arrives even with no model
    //      configured (cause R1); the `answer` handler below reaches the same
    //      `openProposalGate` the fast path uses, so behaviour converges.
    if (looksLikeIntervention(trimmed, INTENT_VOCABULARY)) {
      try {
        await openProposalGate(trimmed, 'regex')
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
        <IconButton
          aria-label="LLM configuration"
          size="small"
          onClick={(e) => setConfigAnchor(e.currentTarget)}
          sx={{ width: 24, height: 24, color: 'text.secondary' }}
        >
          <span style={{ fontSize: 13 }}>⚙</span>
        </IconButton>
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
      <Popover
        open={Boolean(configAnchor)}
        anchorEl={configAnchor}
        onClose={() => setConfigAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <LLMConfigPanel onConfiguredChange={setLlmConfigured} />
      </Popover>

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
              {m.proposals && sceneId && (
                <ProposalReview
                  payload={m.proposals}
                  sceneId={sceneId}
                  onResolved={(outcome, note) => {
                    setMessages((prev) =>
                      prev.map((msg) =>
                        msg.id === m.id && msg.proposals
                          ? {
                              ...msg,
                              proposals: { ...msg.proposals, resolved: outcome },
                              text: note ?? msg.text,
                              chips: outcome === 'applied' ? SUGGESTIONS : msg.chips,
                            }
                          : msg,
                      ),
                    )
                  }}
                />
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
        {/*
          Plan v2 §8 type whitelist. Empty = no restriction (default). A pinned
          chip tells ai-plan to only propose that type. This is the minimal
          template mechanism: it narrows the LLM's search without introducing a
          template schema.
        */}
        <Stack direction="row" spacing={0.5} sx={{ mb: 0.75, flexWrap: 'wrap', gap: 0.5 }}>
          {([
            ['water_reservoir', 'Reservoir'],
            ['green_space', 'Green'],
            ['permeable_paving', 'Paving'],
            ['drainage', 'Drainage'],
            ['road', 'Road'],
          ] as const).map(([kind, label]) => {
            const on = allowedTypes.includes(kind)
            return (
              <Chip
                key={kind}
                label={label}
                size="small"
                variant={on ? 'filled' : 'outlined'}
                color={on ? 'primary' : 'default'}
                onClick={() =>
                  setAllowedTypes((prev) =>
                    prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
                  )
                }
                sx={{ fontSize: 10, height: 20, cursor: 'pointer' }}
              />
            )
          })}
          {allowedTypes.length > 0 && (
            <Chip
              label="clear"
              size="small"
              variant="outlined"
              onClick={() => setAllowedTypes([])}
              sx={{ fontSize: 10, height: 20, cursor: 'pointer', color: 'text.secondary' }}
            />
          )}
        </Stack>
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
