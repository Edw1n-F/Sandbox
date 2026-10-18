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
import { applyAiPlan, createAiPlan, revalidateAiPlan, type AiPlanResult, type InterventionProposal } from '@/api/planApi'
import { INTENT_VOCABULARY, looksLikeIntervention } from '@/api/intentVocabulary'
import { buildReceipt, type Receipt } from '@/api/receipt'
import {
  fetchInputs,
  previewChange,
  fetchRunSuggestions,
  modifyFeature,
  resolveModification,
  resolveRemoval,
  stageFeatures,
  type ModifyTarget,
  type RemovalMatch,
  type SandboxInput,
  type SandboxPreview,
  type SandboxSuggestion,
} from '@/api/sandboxApi'
import InputCard from './InputCard'
import { allAnswered, defaultAnswers, interventionName, toValues, type InputAnswers } from './inputAnswers'
import { usePlanStore } from '@/state/planStore'
import { useSandboxStore } from '@/state/sandboxStore'
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
  /**
   * The SITE-scope questions, asked once for the batch (Phase 2 R8): drainage
   * and tree cover belong to the site, not to a kind, so they travel here
   * rather than repeated on every proposal.
   */
  questions?: NonNullable<AiPlanResult['questions']>
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
  /** Route 1: the grounded suggestions under a run's card (or `loading`). */
  suggestions?: SandboxSuggestion[] | 'loading'
  /** A suggestion the user picked, waiting on its input card. */
  staging?: { suggestion: SandboxSuggestion; resolved?: 'added' | 'cancelled' }
  /** What a removal instruction resolved to, waiting on the user's choice. */
  removal?: { matches: RemovalMatch[]; resolved?: string }
  /** A change to a placed intervention, waiting on its input card. */
  modify?: { target: ModifyTarget; resolved?: boolean }
}

/**
 * Does this ask to take something off the sandbox? A removal verb anywhere in
 * the turn, from the shared vocabulary pinned to the server's. Checked before
 * the add path, which would otherwise read "remove the reservoir" as a request
 * to propose one.
 */
function isRemoval(text: string): boolean {
  const lowered = text.toLowerCase()
  if (lowered.trim().endsWith('?')) return false
  return INTENT_VOCABULARY.removalVerbs.some((verb) =>
    new RegExp(`\\b${verb.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(lowered),
  )
}

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
  const caveat = card.caveats?.[0] ?? ''
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

      {card.confidence && (
        <>
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
        </>
      )}

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
 * One plain line about where and how big a proposal is: what a planner reads
 * before deciding, with the full constraint report folded away under it.
 */
function summaryLine(p: InterventionProposal): string {
  const parts: string[] = []
  if (p.size) parts.push(`${p.size.areaHa} ha`)
  if (p.constraints) {
    parts.push(p.constraints.buildingsClear ? 'clear of buildings' : 'overlaps buildings')
    if (p.constraints.clippedCells > 0) parts.push(`${p.constraints.clippedCells} cells clipped`)
  }
  return parts.join(' · ')
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
    <Box sx={{ mt: 0.25, minWidth: 0 }}>
      {size && (
        <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          size: {size.cells} cells · {size.areaHa} ha · {(size.coverageFraction * 100).toFixed(2)}% of area
        </Typography>
      )}
      {constraints && (
        <Box sx={{ mt: 0.25, minWidth: 0 }}>
          <Typography
            className="hud-mono"
            sx={{ fontSize: 9.5, color: constraints.buildingsClear ? '#5cff9d' : '#ff6b6b', lineHeight: 1.4, overflowWrap: 'anywhere' }}
          >
            buildings: {constraints.buildingsClear ? 'clear' : `overlaps ${constraints.buildingOverlapCells} cell(s)`}
            {constraints.buildingIds.length > 0 ? ` (${constraints.buildingIds.join(', ')})` : ''}
          </Typography>
          <Typography
            className="hud-mono"
            sx={{ fontSize: 9.5, color: constraints.overlapCells === 0 ? 'text.secondary' : '#ff9d2e', lineHeight: 1.4, overflowWrap: 'anywhere' }}
          >
            overlap: {constraints.overlapCells} cell(s)
            {constraints.overlapsWith.length > 0 ? ` with ${constraints.overlapsWith.join(', ')}` : ''}
          </Typography>
          {constraints.clippedCells > 0 && (
            <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4, overflowWrap: 'anywhere' }}>
              clipped: {constraints.clippedCells} cell(s) ({(constraints.clippedFraction * 100).toFixed(1)}%)
            </Typography>
          )}
          <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4, overflowWrap: 'anywhere' }}>
            caps: single {constraints.withinSingleCap ? 'ok' : 'over'} · cumulative {constraints.withinCumulativeCap ? 'ok' : 'over'}
          </Typography>
          <Typography sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.45, mt: 0.25, overflowWrap: 'anywhere' }}>
            effect: {constraints.effect}{constraints.effectNote ? ` — ${constraints.effectNote}` : ''}
          </Typography>
        </Box>
      )}
      {parameters.map(([name, entry]) => (
        <Typography key={name} className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4, overflowWrap: 'anywhere' }}>
          {name}: {entry.value} {entry.unit} · {entry.status} · {entry.source}
          {entry.edited && entry.defaultValue !== null && entry.defaultValue !== undefined
            ? ` (was ${entry.defaultValue} ${entry.unit})`
            : ''}
        </Typography>
      ))}
      {proposal.status && (
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.4, mt: 0.25, overflowWrap: 'anywhere' }}>
          status: {proposal.status}
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
  // The input card per proposal: what the run reads, and the user's answers.
  const [inputs, setInputs] = useState<SandboxInput[][]>(() => payload.proposals.map(() => []))
  const [answers, setAnswers] = useState<InputAnswers[]>(() => payload.proposals.map(() => ({})))
  // What the last re-gate said about the edited set (Phase 2 R19.1).
  const [revalidate, setRevalidate] = useState<{
    warning: string
    valuesOnly: string[]
    movedGeometry: string[]
    refused: number
  } | null>(null)
  // What the preview measured (Phase 4 T01/T02). Held beside the draft set, not
  // in it: a preview is a reading, not a proposal.
  const [preview, setPreview] = useState<SandboxPreview | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)

  useEffect(() => {
    if (payload.resolved) return
    let live = true
    void Promise.all(
      payload.proposals.map((p) =>
        fetchInputs(sceneId, p.interventionType, p.params ?? {}).catch(() => [] as SandboxInput[]),
      ),
    ).then((all) => {
      if (!live) return
      setInputs(all)
      setAnswers(all.map(defaultAnswers))
    })
    return () => {
      live = false
    }
  }, [payload, sceneId])

  // Show what the edit did before Apply (Phase 2 R19.1). Debounced because a
  // planner types a depth one character at a time; the call writes nothing and
  // spends no provider call, but a request per character is still that.
  //
  // The proposals are sent with the typed values folded into `params`, because
  // `params.storageDepthM` is the key the ledger reads — re-checking the
  // unedited params would report on a proposal the planner is no longer
  // looking at.
  const editedKey = JSON.stringify(answers)
  useEffect(() => {
    if (payload.resolved) return
    const checkedNow = payload.proposals.map((_, i) => checked[i] ?? true)
    if (!checkedNow.some(Boolean)) return
    if (payload.proposals.some((_, i) =>
      checkedNow[i] && !allAnswered(inputs[i] ?? [], answers[i]))) return
    const edited = payload.proposals.map((p, i) => {
      const typed = toValues(inputs[i] ?? [], answers[i] ?? {})
      return Object.keys(typed).length === 0
        ? p
        : { ...p, params: { ...(p.params ?? {}), ...typed } }
    })
    const timer = setTimeout(() => {
      void revalidateAiPlan(sceneId, {
        proposals: edited,
        instruction: payload.instruction,
      })
        .then((r) => setRevalidate({
          warning: r.warning,
          valuesOnly: r.valuesAffectingOnly,
          movedGeometry: r.geometryAffectingParameters,
          refused: (r.rejected ?? []).length,
        }))
        // A re-gate that fails must not block Apply: the Apply path
        // re-validates anyway (R19), so this is reported as "could not
        // re-check" rather than as a reason to refuse the edit.
        .catch(() => setRevalidate({
          warning: 'Could not re-check the edit before applying. Apply still validates it.',
          valuesOnly: [], movedGeometry: [], refused: 0,
        }))
    }, 350)
    return () => clearTimeout(timer)
    // `editedKey` stands in for `answers`, whose identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload, sceneId, inputs, editedKey, checked])

  if (payload.resolved === 'applied') {
    return (
      <Typography
        variant="caption"
        sx={{ display: 'block', mt: 1, color: 'text.secondary' }}
      >
        Added to the sandbox. Run again to see the effect.
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
  // A checked proposal with a field the run would refuse holds Apply back.
  const invalid = payload.proposals.some(
    (_, i) => checked[i] && !allAnswered(inputs[i], answers[i]),
  )

  // Measure the checked candidates without applying them (Phase 4 T01). Writes
  // nothing and calls no model; the reading is held beside the draft set, never
  // in it, and it is cleared when the set changes so a stale number cannot be
  // read next to a proposal it no longer describes.
  const runPreview = async () => {
    const indices = payload.proposals.map((_, i) => i).filter((i) => checked[i])
    if (indices.length === 0 || invalid) return
    setPreviewBusy(true)
    setError(null)
    try {
      setPreview(await previewChange(sceneId, {
        interventions: indices.map((i) => payload.proposals[i] as unknown as Record<string, unknown>),
        values: indices.map((i) => toValues(inputs[i], answers[i])),
        planIds: [],
      }))
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setPreviewBusy(false)
    }
  }

  const apply = async () => {
    const indices = payload.proposals.map((_, i) => i).filter((i) => checked[i])
    if (indices.length === 0 || invalid) return
    setBusy(true)
    setError(null)
    try {
      const r = await applyAiPlan(sceneId, {
        instruction: payload.instruction,
        provider: payload.provider,
        proposals: indices.map((i) => payload.proposals[i]),
        groundingNotes: payload.groundingNotes,
        candidateCounts: payload.candidateCounts,
        values: indices.map((i) => toValues(inputs[i], answers[i])),
      })
      if (r.planId) {
        // The draft is now on the sandbox: drop the dashed preview, and re-read
        // the sandbox so the panel, the 3D view and the next run all have it.
        usePlanStore.getState().clearProposals()
        await useSandboxStore.getState().refresh(sceneId)
        onResolved(
          'applied',
          `Added ${r.features} intervention${r.features === 1 ? '' : 's'} (${(r.types ?? []).join(', ') || 'geometry'}) to the sandbox. Run again to see the effect.`,
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
            <Box sx={{ minWidth: 0 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
                {interventionName(p.interventionType)}
              </Typography>
              <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
                {summaryLine(p)}
              </Typography>
              {p.rationale && (
                <Typography sx={{ fontSize: 10.5, color: 'text.secondary', lineHeight: 1.35, mt: 0.25, overflowWrap: 'anywhere' }}>
                  {p.rationale}
                </Typography>
              )}
              {/* The proposal's own honesty label: that the geometry is a
                  screening contour, what was clipped and why, and -- once edited
                  -- that the depth is hand-set rather than the class default. It
                  stays outside the folded evidence on purpose: a gate that shows
                  the numbers and hides this sentence is the overclaiming this
                  project is built to prevent. */}
              {p.warning && (
                <Typography sx={{ fontSize: 9.5, color: 'rgba(255,157,46,0.9)', lineHeight: 1.45, mt: 0.25, overflowWrap: 'anywhere' }}>
                  {p.warning}
                </Typography>
              )}
              {checked[i] && (
                // Not part of the checkbox label's click target: typing a depth
                // must not toggle the proposal off.
                <Box
                  sx={{ mt: 0.75, p: 0.75, minWidth: 0, borderRadius: 1, border: '1px solid', borderColor: 'divider' }}
                  onClick={(e) => e.preventDefault()}
                >
                  <Typography className="hud-label" sx={{ fontSize: 9, color: 'text.secondary', mb: 0.5 }}>
                    DECIDE THE DETAILS
                  </Typography>
                  <InputCard
                    inputs={inputs[i]}
                    answers={answers[i]}
                    disabled={busy}
                    onChange={(next) =>
                      setAnswers((prev) => prev.map((old, idx) => (idx === i ? next : old)))
                    }
                  />
                </Box>
              )}
              <Box component="details" sx={{ mt: 0.5 }} onClick={(e) => e.stopPropagation()}>
                <Box component="summary" sx={{ fontSize: 9.5, color: 'text.secondary', cursor: 'pointer' }}>
                  Evidence and constraints
                </Box>
                {p.basis && (
                  <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.3, overflowWrap: 'anywhere' }}>
                    basis: {p.basis}
                  </Typography>
                )}
                <ProposalDetail proposal={p} />
              </Box>
            </Box>
          }
          sx={{ alignItems: 'flex-start', ml: 0, mr: 0, minWidth: 0 }}
        />
      ))}
      <RefusalList rejected={payload.rejected} />
      {revalidate && (
        <Box sx={{ mt: 0.5 }}>
          <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
            {revalidate.movedGeometry.length === 0
              ? 're-check: no parameter here moves geometry'
              : `re-check: ${revalidate.movedGeometry.join(', ')} changes the placement`}
            {revalidate.valuesOnly.length > 0
              ? ` · value-only: ${revalidate.valuesOnly.join(', ')}`
              : ''}
            {revalidate.refused > 0 ? ` · ${revalidate.refused} would now be refused` : ''}
          </Typography>
          {revalidate.refused > 0 && (
            <Typography sx={{ fontSize: 10, color: '#ff6b6b', lineHeight: 1.4 }}>
              {revalidate.warning}
            </Typography>
          )}
        </Box>
      )}
      {error && (
        <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>
      )}
      <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
        <Button
          size="small"
          variant="contained"
          disabled={!anyChecked || busy || invalid}
          onClick={() => void apply()}
          sx={{ fontSize: 11 }}
        >
          {busy ? 'Adding…' : `Add ${checked.filter(Boolean).length} to sandbox`}
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={!anyChecked || busy || previewBusy || invalid}
          onClick={() => void runPreview()}
          sx={{ fontSize: 11 }}
        >
          {previewBusy ? 'Measuring…' : 'Preview effect'}
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
      {preview && <PreviewReading preview={preview} />}
    </Stack>
  )
}

/**
 * The preview's numbers, with the three deltas kept apart (Phase 4 §1.4).
 *
 * `status`, `warning` and `scope` are rendered **verbatim** from the payload
 * rather than re-worded in the UI: a preview whose numbers look like a run's is
 * the failure this whole endpoint exists to prevent, and a UI-only convention
 * would leave the payload able to mislead any other client.
 *
 * The three figures are labelled by what they are the difference *of*, and the
 * marginal one is never shown under the run's key — once prior work exists they
 * differ by exactly that prior work, so quoting one as the other is a wrong
 * number under a right-sounding label.
 */
function PreviewReading({ preview }: { preview: SandboxPreview }) {
  const marginal = preview.previewDelta ?? {}
  const runDelta = preview.runDelta ?? {}
  return (
    <Box
      sx={{
        mt: 0.75,
        p: 0.75,
        borderRadius: '4px',
        border: '1px solid',
        borderColor: 'rgba(92,255,157,0.35)',
        bgcolor: 'rgba(92,255,157,0.06)',
      }}
    >
      <Typography className="hud-label" sx={{ fontSize: 9, color: '#5cff9d' }}>
        {preview.status}
      </Typography>
      <Typography sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.45, mt: 0.25 }}>
        {preview.warning}
      </Typography>
      <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.4, mt: 0.25 }}>
        scope: {preview.scope}
      </Typography>
      <Typography className="hud-mono" sx={{ fontSize: 9.5, lineHeight: 1.5, mt: 0.5 }}>
        marginal (these candidates, on top of what is on the sandbox):
        {' '}unstored {fmt(marginal.unstoredM3)} m³ · runoff {fmt(marginal.runoffM3)} m³ ·
        {' '}capacity {fmt(marginal.storageCapacityM3)} m³
      </Typography>
      <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.5 }}>
        the run&rsquo;s own delta (scenario vs today&rsquo;s land cover, prior work included):
        {' '}unstored {fmt(runDelta.deltaUnstoredM3)} m³ · runoff {fmt(runDelta.deltaRunoffM3)} m³
      </Typography>
      <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.4 }}>
        now: unstored {fmt(preview.now?.unstoredM3)} m³ · capacity {fmt(preview.now?.storageCapacityM3)} m³
        {' → '}with the change: unstored {fmt(preview.preview?.unstoredM3)} m³ ·
        {' '}capacity {fmt(preview.preview?.storageCapacityM3)} m³
      </Typography>
      {(preview.interventionsSkipped ?? []).length > 0 && (
        <Typography sx={{ fontSize: 9.5, color: '#ff9d2e', lineHeight: 1.4, mt: 0.25 }}>
          the run would skip {preview.interventionsSkipped.length} of these:
          {' '}{(preview.interventionsSkipped as Array<{ why?: string }>).map((s) => s.why).filter(Boolean).join('; ')}
        </Typography>
      )}
      <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.4, mt: 0.25 }}>
        {preview.ledgerSource}
      </Typography>
    </Box>
  )
}

/** A ledger figure with a sign and no false precision. */
function fmt(value: number | undefined): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '—'
  const rounded = Math.round(value)
  return `${rounded > 0 ? '+' : ''}${rounded.toLocaleString()}`
}

/** A suggestion as a draft proposal, so the viewer can draw its dashed preview. */
function asDraft(s: SandboxSuggestion): InterventionProposal {
  return {
    geometry: s.feature.geometry,
    interventionType: s.kind,
    source: 'manual',
    params: s.feature.params,
    style: s.feature.style,
    siteId: null,
    rationale: null,
    basis: s.evidence,
  }
}

/** The estimate a suggestion was ranked on, in the axis's own units. */
function estimateLine(s: SandboxSuggestion): string {
  const e = s.estimate
  if (s.axis === 'drainage') return `adds ~${Math.round(e.storageAddedM3).toLocaleString('en-US')} m³ storage`
  if (s.axis === 'flood') return `${e.floodRisk != null ? Math.round(e.floodRisk).toLocaleString('en-US') : '—'} m³ inflow`
  if (s.axis === 'heat') return `heat index ${e.heatIndexDelta > 0 ? '+' : ''}${e.heatIndexDelta}`
  return `runoff Δ ${e.runoffDeltaM3 > 0 ? '+' : ''}${Math.round(e.runoffDeltaM3)} m³`
}

/**
 * Route 1: a suggestion the user picked, with its input card.
 *
 * The footprint is the suggestion's own, carried verbatim from the rasters; only
 * the values the run reads are asked for. Add pins it to the sandbox.
 */
function SuggestionStage({
  staging,
  sceneId,
  onResolved,
}: {
  staging: NonNullable<Message['staging']>
  sceneId: string
  onResolved: (outcome: 'added' | 'cancelled', note: string) => void
}) {
  const { suggestion } = staging
  const [answers, setAnswers] = useState<InputAnswers>(() => defaultAnswers(suggestion.inputs))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const invalid = !allAnswered(suggestion.inputs, answers)

  if (staging.resolved) return null

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      await stageFeatures(sceneId, [suggestion.feature], [toValues(suggestion.inputs, answers)])
      usePlanStore.getState().clearProposals()
      await useSandboxStore.getState().refresh(sceneId)
      onResolved('added', `Added ${suggestion.label.replace(/^Add /, '')} to the sandbox. Run again to see the effect.`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={0.75} sx={{ mt: 1 }}>
      <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
        {suggestion.evidence} · {estimateLine(suggestion)}
      </Typography>
      <InputCard inputs={suggestion.inputs} answers={answers} onChange={setAnswers} disabled={busy} />
      {error && <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>}
      <Stack direction="row" spacing={1}>
        <Button size="small" variant="contained" disabled={busy || invalid} onClick={() => void add()} sx={{ fontSize: 11 }}>
          {busy ? 'Adding…' : 'Add to sandbox'}
        </Button>
        <Button
          size="small"
          variant="outlined"
          disabled={busy}
          onClick={() => {
            usePlanStore.getState().clearProposals()
            onResolved('cancelled', 'Cancelled. Nothing was added.')
          }}
          sx={{ fontSize: 11 }}
        >
          Cancel
        </Button>
      </Stack>
    </Stack>
  )
}

/**
 * Removal: what an instruction resolved to, with a confirmation per match.
 *
 * An intervention is unpinned; a planned project is toggled off, never deleted.
 * Several matches ("remove the park" can be HSK Park or a green space the user
 * added) are listed so the user picks one rather than the app guessing.
 */
function RemovalReview({
  removal,
  sceneId,
  onResolved,
}: {
  removal: NonNullable<Message['removal']>
  sceneId: string
  onResolved: (note: string) => void
}) {
  const [busy, setBusy] = useState(false)
  if (removal.resolved || removal.matches.length === 0) return null

  const act = async (m: RemovalMatch) => {
    setBusy(true)
    try {
      if (m.action === 'unpin' && m.featureId) {
        await useSandboxStore.getState().remove(sceneId, m.featureId)
        onResolved(`Removed ${m.label} from the sandbox. Run again to see the effect.`)
      } else if (m.action === 'toggle_off' && m.planId) {
        const { selectedPlanIds, setSelectedPlanIds } = usePlanStore.getState()
        setSelectedPlanIds(selectedPlanIds.filter((id) => id !== m.planId))
        onResolved(`Toggled ${m.label} off. Its plan is kept, so you can toggle it back on in Projects.`)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={0.5} sx={{ mt: 1 }}>
      {removal.matches.map((m) => (
        <Stack key={m.featureId ?? m.planId} direction="row" spacing={1} alignItems="center" justifyContent="space-between">
          <Typography sx={{ fontSize: 11.5 }}>
            {m.label}
            <Box component="span" sx={{ color: 'text.secondary', fontSize: 10 }}>
              {m.type === 'project' ? ' · planned project' : ' · placed intervention'}
            </Box>
          </Typography>
          <Button size="small" color="error" variant="outlined" disabled={busy} onClick={() => void act(m)} sx={{ fontSize: 10.5 }}>
            {m.action === 'toggle_off' ? 'Toggle off' : 'Remove'}
          </Button>
        </Stack>
      ))}
      <Button size="small" disabled={busy} onClick={() => onResolved('Kept everything as it was.')} sx={{ fontSize: 10.5, alignSelf: 'flex-start' }}>
        Keep everything
      </Button>
    </Stack>
  )
}

/**
 * Modification: the input card for an intervention already on the sandbox.
 *
 * A value the sentence named ("5 m deep") opens as the user's own; the rest
 * keep what the row already carries.
 */
function ModifyReview({
  modify,
  sceneId,
  onResolved,
}: {
  modify: NonNullable<Message['modify']>
  sceneId: string
  onResolved: (note: string) => void
}) {
  const { target } = modify
  const [answers, setAnswers] = useState<InputAnswers>(() => {
    const start = defaultAnswers(target.inputs)
    for (const [key, value] of Object.entries(target.suggestedValues)) {
      start[key] = { mode: 'user', text: String(value) }
    }
    return start
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (modify.resolved) return null
  const invalid = !allAnswered(target.inputs, answers)

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await modifyFeature(sceneId, target.featureId, toValues(target.inputs, answers))
      await useSandboxStore.getState().refresh(sceneId)
      onResolved(`Changed the ${target.label}. Run again to see the effect.`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={0.75} sx={{ mt: 1 }}>
      <InputCard inputs={target.inputs} answers={answers} onChange={setAnswers} disabled={busy} />
      {error && <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>}
      <Stack direction="row" spacing={1}>
        {target.inputs.length > 0 && (
          <Button size="small" variant="contained" disabled={busy || invalid} onClick={() => void save()} sx={{ fontSize: 11 }}>
            {busy ? 'Saving…' : 'Save change'}
          </Button>
        )}
        <Button size="small" variant="outlined" disabled={busy} onClick={() => onResolved('Left it as it was.')} sx={{ fontSize: 11 }}>
          Cancel
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
      text: 'Sandbox loaded. Adjust the rainfall and temperature sliders (top-right), toggle a project, then press Run. After the run I will suggest three interventions grounded in it, and you can describe your own here.',
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
    // Route 1: the three suggestions under the card are grounded sites for
    // THIS run, fetched once it returns (ranking takes seconds the run should
    // not wait on). Fixed sentences used to sit here, the same on every run.
    const messageId = mkId()
    setMessages((prev) => [
      ...prev,
      {
        id: messageId,
        role: 'ai' as const,
        ...(runCard ? { card: runCard } : { text: runResult.explanation }),
        receipt: buildReceipt(runResult),
        suggestions: 'loading' as const,
      },
    ])
    if (!sceneId) return
    const setSuggestions = (suggestions: SandboxSuggestion[]) =>
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, suggestions } : m)))
    void fetchRunSuggestions(sceneId, runResult.inputs.rainfallMm, runResult.inputs.planIds ?? [])
      .then((r) => setSuggestions(r.suggestions))
      .catch(() => setSuggestions([]))
  }, [runResult, running, sceneId])

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
              questions: r.questions ?? [],
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
          // An intervention instruction does not answer a question about the
          // run: it routes to the design loop. Rendering a chat card here put
          // "No project is active…" in the thread next to the ai-plan gate, so
          // the two are separated -- the intervention path skips the card and
          // opens the gate directly.
          if (event.answer.intent === 'intervention' && event.answer.intent_hint) {
            void openProposalGate(event.answer.intent_hint, 'intent')
            break
          }
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
    // A removal is resolved against what is on the sandbox -- placed
    // interventions and planned projects -- never sent to the proposer, which
    // would read "remove the reservoir" as a request to add one.
    if (isRemoval(trimmed)) {
      try {
        const { matches, message } = await resolveRemoval(sceneId, trimmed)
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'ai',
            text:
              message ??
              (matches.length === 1
                ? `This would ${matches[0].action === 'toggle_off' ? 'toggle off' : 'remove'} ${matches[0].label}. Confirm below.`
                : ''),
            removal: { matches },
          },
        ])
      } catch (err) {
        setMessages((prev) => [...prev, { id: mkId(), role: 'ai', text: `Error: ${errorMessage(err)}` }])
      } finally {
        setBusy(false)
      }
      return
    }

    // A change to something already placed ("make the reservoir 5 m deep")
    // edits that row in place. Anything else falls through to adding.
    if (looksLikeIntervention(trimmed, INTENT_VOCABULARY) || /\b\d+(\.\d+)?\s*m\b/i.test(trimmed)) {
      try {
        const mod = await resolveModification(sceneId, trimmed)
        if (mod.isModification && mod.targets?.length) {
          setMessages((prev) => [
            ...prev,
            {
              id: mkId(),
              role: 'ai',
              text: `This changes the ${mod.targets![0].label} already on the sandbox; its footprint stays where it is.`,
              modify: { target: mod.targets![0] },
            },
          ])
          setBusy(false)
          return
        }
      } catch {
        // Not resolvable as a change: treat it as an addition below.
      }
    }

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

  /** Route 1: open the input card for one of the run's suggestions, with a 3D preview. */
  const pickSuggestion = (s: SandboxSuggestion) => {
    usePlanStore.getState().setProposals([asDraft(s)])
    setMessages((prev) => [
      ...prev,
      { id: mkId(), role: 'user', text: s.label },
      {
        id: mkId(),
        role: 'ai',
        text:
          s.inputs.length > 0
            ? 'Set the values the run will use, or let the AI estimate them. The dashed block shows where it goes.'
            : 'The dashed block shows where it goes. Nothing to set for this kind.',
        staging: { suggestion: s },
      },
    ])
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
                maxWidth: '100%',
                minWidth: 0,
                overflow: 'hidden',
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
                  sx={{ fontSize: 12.5, lineHeight: 1.55, whiteSpace: 'pre-wrap', color: 'text.primary', overflowWrap: 'anywhere' }}
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
              {m.suggestions === 'loading' && (
                <Typography className="hud-shimmer" variant="caption" sx={{ display: 'block', mt: 1 }}>
                  Finding three sites for this run…
                </Typography>
              )}
              {Array.isArray(m.suggestions) && (
                <Box sx={{ mt: 1 }}>
                  <Typography className="hud-label" sx={{ fontSize: 9, mb: 0.5 }}>
                    SUGGESTED FROM THIS RUN
                  </Typography>
                  {m.suggestions.length === 0 ? (
                    <Typography sx={{ fontSize: 10.5, color: 'text.secondary' }}>
                      No free site to suggest. Describe an intervention below instead.
                    </Typography>
                  ) : (
                    <Stack spacing={0.5}>
                      {m.suggestions.map((s) => (
                        <Button
                          key={`${s.axis}-${s.kind}`}
                          size="small"
                          variant="outlined"
                          disabled={busy}
                          onClick={() => pickSuggestion(s)}
                          sx={{ justifyContent: 'flex-start', textAlign: 'left', textTransform: 'none', fontSize: 11, py: 0.4 }}
                        >
                          <Box>
                            <Box>{s.label}</Box>
                            <Box sx={{ fontSize: 9.5, color: 'text.secondary' }}>
                              {s.axis} · {estimateLine(s)}
                            </Box>
                          </Box>
                        </Button>
                      ))}
                    </Stack>
                  )}
                </Box>
              )}
              {m.staging && sceneId && (
                <SuggestionStage
                  staging={m.staging}
                  sceneId={sceneId}
                  onResolved={(outcome, note) =>
                    setMessages((prev) =>
                      prev.map((msg) =>
                        msg.id === m.id && msg.staging
                          ? { ...msg, text: note, staging: { ...msg.staging, resolved: outcome } }
                          : msg,
                      ),
                    )
                  }
                />
              )}
              {m.modify && sceneId && (
                <ModifyReview
                  modify={m.modify}
                  sceneId={sceneId}
                  onResolved={(note) =>
                    setMessages((prev) =>
                      prev.map((msg) =>
                        msg.id === m.id && msg.modify
                          ? { ...msg, text: note, modify: { ...msg.modify, resolved: true } }
                          : msg,
                      ),
                    )
                  }
                />
              )}
              {m.removal && sceneId && (
                <RemovalReview
                  removal={m.removal}
                  sceneId={sceneId}
                  onResolved={(note) =>
                    setMessages((prev) =>
                      prev.map((msg) =>
                        msg.id === m.id && msg.removal
                          ? { ...msg, text: note, removal: { ...msg.removal, resolved: note } }
                          : msg,
                      ),
                    )
                  }
                />
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
