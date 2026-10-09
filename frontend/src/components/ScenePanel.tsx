/**
 * Scene parameter panel (spec §1.1 `components/ScenePanel.tsx`, item C6).
 *
 * Status plus the rainfall / temperature sliders. Per CLAUDE.md this layer only
 * "triggers state changes, no rendering logic" -- it never renders the map.
 */
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Paper from '@mui/material/Paper'
import MenuItem from '@mui/material/MenuItem'
import Select, { type SelectChangeEvent } from '@mui/material/Select'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { useEffect, useState } from 'react'

import { createAiPlan, type AiPlanResult } from '@/api/planApi'
import ImpactPanel from '@/components/ImpactPanel'
import {
  cityModelStats,
  editCityModelHeights,
  resetCityModel,
  selectCityBuilding,
  setCityModelBuildingHeight,
  useCityModelRevision,
} from '@/view/SandboxViewer'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import SliderField from './SliderField'

export default function ScenePanel() {
  const scene = useSceneStore((s) => s.scene)
  const error = useSceneStore((s) => s.error)
  const setEnvParams = useSceneStore((s) => s.setEnvParams)
  const plans = usePlanStore((s) => s.plans)
  const activePlanId = usePlanStore((s) => s.activePlanId)
  const features = usePlanStore((s) => s.interventions.features.length)
  const plan = plans.find((p) => p.planId === activePlanId)
  // A massing edit is an AI proposal like any other, so it is shown with the
  // same posture ADR-030 requires: named, attributed, and reversible. A plan can
  // carry several -- "place three towers and make the rest 20% shorter" is two --
  // so every one of them is listed rather than only the first.
  const interventionFeatures = usePlanStore((s) => s.interventions.features)
  const massingEdits = interventionFeatures.filter(
    (f) => f.properties?.interventionType === 'massing_height',
  )
  /** One line per operation, from the op itself plus what the viewer reported. */
  const massingLines = massingEdits.map((f) => {
    const p = f.properties?.params as
      | {
          op?: string
          multiplier?: number
          heightM?: number
          deltaM?: number
          degrees?: number
          dxM?: number
          dyM?: number
          count?: number
          rationale?: string
          note?: string
          scope?: { label?: string }
        }
      | undefined
    const scope = p?.scope?.label ?? 'the whole study area'
    let what = 'an unrecognised operation'
    switch (p?.op) {
      case 'scale_all':
        what = `scale ×${p.multiplier}`
        break
      case 'set_site':
        what = typeof p.deltaM === 'number'
          ? `raise ${p.deltaM > 0 ? '+' : ''}${p.deltaM} m`
          : `set ${p.heightM} m`
        break
      case 'rotate':
        what = `rotate ${p.degrees}°`
        break
      case 'move': {
        // Compass, because "dx 12, dy -30" is not something a planner reads.
        const dx = Number(p.dxM ?? 0)
        const dy = Number(p.dyM ?? 0)
        const ns = dy > 0 ? 'north' : dy < 0 ? 'south' : ''
        const ew = dx > 0 ? 'east' : dx < 0 ? 'west' : ''
        const bearing = [ns, ew].filter(Boolean).join('-') || '—'
        const distance = Math.round(Math.hypot(dx, dy))
        what = `move ${distance} m ${bearing}`
        break
      }
      case 'place':
        what = `place ${p.count} block${p.count === 1 ? '' : 's'}${p.heightM ? ` at ${p.heightM} m` : ''}`
        break
      case 'reset':
        what = 'reset to generated heights'
        break
      default:
        break
    }
    return { key: f.properties?.id ?? what, scope, what, op: p?.op,
      rationale: p?.rationale || undefined }
  })
  const selectPlan = usePlanStore((s) => s.selectPlan)
  const refreshPlans = usePlanStore((s) => s.refreshPlans)
  const sceneId = useSceneStore((s) => s.sceneId)
  const [instruction, setInstruction] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [aiResult, setAiResult] = useState<AiPlanResult | null>(null)
  // Read from /health so the badge reflects the *server's* configuration, not
  // what this browser last happened to run. A demo on a machine with no key must
  // say so before a judge draws the conclusion, not after.
  /**
   * Tri-state, not a nullable object.
   *
   * `null` used to mean two different things -- "no key configured" and "could not
   * reach /health" -- and the badge collapsed both to "no LLM key". That states
   * something false: a reachable backend with no key is a configuration problem the
   * user can fix, whereas an unreachable one means the badge is telling them nothing
   * because there is nothing to tell. `CLAUDE.md` makes the general point about a demo
   * quietly not being AI; the same honesty applies to not knowing.
   */
  const [llm, setLlm] = useState<{
    configured: boolean
    model: string | null
  } | null>(null)
  const [llmUnreachable, setLlmUnreachable] = useState(false)
  useEffect(() => {
    let cancelled = false
    void fetch('/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`status ${r.status}`))))
      .then((d) => {
        if (cancelled) return
        setLlmUnreachable(false)
        if (d?.llm) setLlm(d.llm)
      })
      .catch(() => {
        // Leave `llm` null *and* record that we could not ask, so the badge can say so.
        if (!cancelled) setLlmUnreachable(true)
      })
    return () => {
      cancelled = true
    }
  }, [aiResult])
  const [aiError, setAiError] = useState<string | null>(null)
  const terrain = useSceneStore((s) => s.terrain)
  // The city model loads asynchronously, so reading the stats once at mount
  // captured an empty model and the editing panel never appeared. Re-read them
  // whenever the viewer's revision counter moves -- which happens both on load
  // and on every pick.
  const cityRev = useCityModelRevision()
  const [city, setCity] = useState(() => cityModelStats())
  useEffect(() => {
    setCity(cityModelStats())
  }, [cityRev])

  /**
   * Ask the AI module for a plan, then select it.
   *
   * Selecting the new plan is the whole point: it routes the generated features
   * through the existing store and render path, so the 3D scene updates with no
   * special-casing for AI output. That is §3.3.2's "one pipeline" requirement
   * made visible in the UI.
   */
  // The heuristic provider is a regex parser, and calling it AI would be the one
  // claim in this project that is simply false. So the badge and the copy both
  // name it for what it is.
  const aiIsLive = Boolean(aiResult && aiResult.provider !== 'heuristic')

  const runInstruction = async () => {
    const text = instruction.trim()
    if (!text || aiBusy || !sceneId) return
    setAiBusy(true)
    setAiError(null)
    setAiResult(null)
    try {
      const result = await createAiPlan(sceneId, text)
      setAiResult(result)
      if (result.planId) {
        // Refresh the list first, then select: the panel derives the plan's
        // label and type from the list, so selecting before the list knows
        // about the plan renders "No plan" beside a populated intervention
        // count.
        await refreshPlans(sceneId)
        await selectPlan(result.planId)
        setInstruction('')
      }
    } catch (err) {
      setAiError(err instanceof Error ? err.message : String(err))
    } finally {
      setAiBusy(false)
    }
  }
  const loading = usePlanStore((s) => s.loading)

  return (
    <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Box>
        <Typography variant="overline" color="text.secondary">
          Scene
        </Typography>
        <Typography variant="h6" sx={{ lineHeight: 1.2 }}>
          {scene?.name ?? 'No scene'}
        </Typography>
        {scene && (
          <Typography variant="caption" color="text.secondary" display="block">
            {scene.center.coordinates[1].toFixed(4)}°N,{' '}
            {scene.center.coordinates[0].toFixed(4)}°E
          </Typography>
        )}
      </Box>

      {error && <Alert severity="error" variant="outlined">{error}</Alert>}

      {scene?.terrainNote && (
        <Alert severity="info" variant="outlined" sx={{ py: 0 }}>
          <Typography variant="caption">{scene.terrainNote}</Typography>
        </Alert>
      )}

      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle2">Plan</Typography>
        <Chip size="small" label={plan?.planType ?? '—'} />
      </Stack>

      {/* Plan selection, not just a label.
          A scenario tool where you cannot pick a scenario is not a scenario
          tool: the store picks the baseline by default, and the baseline is
          empty by definition, so without this the interventions layer would
          always show "none in plan" and there would be no way to reach the
          other plans at all. */}
      {plans.length > 1 ? (
        <Select
          size="small"
          value={activePlanId ?? ''}
          disabled={loading}
          // The adjacent "Plan" caption is visual only, so the combobox needs its own
          // accessible name or a screen reader announces an unlabelled combo box.
          // On the `Select` itself, not `slotProps.input`: MUI also renders a *hidden*
          // native input for form submission, and labelling that one achieves nothing
          // visible while still reading as a failure in an audit.
          aria-label="Active plan"
          onChange={(event: SelectChangeEvent) =>
            void selectPlan(event.target.value)
          }
          displayEmpty
        >
          {plans.map((p) => (
            <MenuItem key={p.planId} value={p.planId}>
              {p.label ?? p.planType}
            </MenuItem>
          ))}
        </Select>
      ) : null}

      <Typography variant="caption" color="text.secondary">
        {plan?.label ?? 'No plan'} · {features} feature{features === 1 ? '' : 's'}
      </Typography>

      {city.count > 0 && (
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="subtitle2">3D city model</Typography>
            <Chip size="small" color="warning" label="provisional" />
          </Stack>
          <Typography variant="caption" color="text.secondary" display="block">
            {city.count} massing envelopes · mean {city.mean.toFixed(1)} m
            {city.placed ? ` · ${city.placed} added by the plan` : ''}
            {city.selected ? ` · selected ${city.selected}` : ''}
          </Typography>

          <Typography variant="caption" color="text.secondary" display="block">
            {terrain?.cityModelMeta?.note ?? 'Massing derived from land cover.'}
          </Typography>

          {/* Whether the envelopes stand on the terrain is a materially different
              claim from "these are the buildings", so it is stated rather than left
              to be inferred from the render. Before this was wired the bases were
              all zero and every uphill envelope was buried inside a hill, which the
              picture did not reveal. */}
          <Typography variant="caption" color="text.secondary" display="block">
            {city.grounded === city.count
              ? `Standing on the terrain (${city.grounded} of ${city.count} sampled from the DEM).`
              : city.grounded > 0
                ? `Standing on the terrain where the DEM covers it (${city.grounded} of ${city.count}).`
                : 'Not yet placed on the terrain: no elevation under these footprints.'}
          </Typography>

          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <Button size="small" variant="outlined"
              onClick={() => { editCityModelHeights(1.25); setCity(cityModelStats()) }}>
              +25% height
            </Button>
            <Button size="small" variant="outlined"
              onClick={() => { editCityModelHeights(0.8); setCity(cityModelStats()) }}>
              -20% height
            </Button>
            <Button size="small" color="inherit"
              onClick={() => { resetCityModel(); selectCityBuilding(null); setCity(cityModelStats()) }}>
              Reset
            </Button>
          </Stack>

          {city.selected && (
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
              <Typography variant="caption">Selected</Typography>
              <TextField
                size="small" type="number" defaultValue={0} sx={{ width: 90 }}
                label="metres"
                onChange={(e) => {
                  const m = Number(e.target.value)
                  if (m > 0) { setCityModelBuildingHeight(city.selected as string, m); setCity(cityModelStats()) }
                }}
              />
            </Stack>
          )}
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
            Click a building to select it, then edit its height directly.
          </Typography>
        </Paper>
      )}

      {massingLines.length > 0 && (
        <Alert severity="info" variant="outlined" sx={{ py: 0 }}>
          {massingLines.map((line) => {
            // `city.ops` is what the *viewer* did, not what the backend asked
            // for. Reporting "412 of 2,639 buildings" is the only way a scoped
            // edit can be trusted from the outside: a 3D view looks the same
            // whether the op reached 400 buildings or all 2,639.
            const applied = city.ops.filter((o) => o.op === line.op).pop()
            return (
              <Typography variant="caption" display="block" key={line.key}>
                <strong>AI 3D edit:</strong> {line.what} in {line.scope}
                {applied
                  ? ` — ${applied.changed} of ${applied.total} buildings`
                  : ''}
                {line.rationale ? `. ${line.rationale}` : ''}
              </Typography>
            )
          })}
          <Typography variant="caption" color="text.secondary" display="block">
            Applied to the live model{city.placed ? `, ${city.placed} block${city.placed === 1 ? '' : 's'} added` : ''};
            switch plans to revert.
          </Typography>
        </Alert>
      )}

      <Paper variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="subtitle2" gutterBottom>
          Propose interventions
        </Typography>
        <TextField
          fullWidth
          size="small"
          multiline
          minRows={2}
          placeholder="e.g. add three 60 m residential towers in the east, or make the western blocks 30% taller"
          value={instruction}
          disabled={!sceneId || aiBusy}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void runInstruction()
          }}
        />
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
          <Button
            size="small"
            variant="contained"
            disabled={!sceneId || aiBusy || !instruction.trim()}
            onClick={() => void runInstruction()}
          >
            {aiBusy ? 'Generating…' : 'Generate'}
          </Button>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              size="small"
              color={aiIsLive ? 'success' : 'warning'}
              label={
                aiResult
                  ? aiResult.provider
                  : llmUnreachable
                    ? 'LLM status unknown'
                    : llm?.configured
                      ? 'LLM ready'
                      : 'no LLM key'
              }
            />
            {aiResult && !aiIsLive && (
              <Typography variant="caption" color="text.secondary">
                deterministic parser — <strong>not AI</strong>
              </Typography>
            )}
            {!aiResult && llm && !llm.configured && (
              <Typography variant="caption" color="text.secondary">
                set ANTHROPIC_API_KEY or LLM_BASE_URL for a live model
              </Typography>
            )}
            {!aiResult && llmUnreachable && (
              <Typography variant="caption" color="text.secondary">
                /health did not respond, so whether a model is configured is unknown.
                Plans will still be generated, by the deterministic parser.
              </Typography>
            )}
          </Stack>
          {aiResult && (
            <Typography variant="caption" color="text.secondary" display="block">
              {aiResult.features} feature{aiResult.features === 1 ? '' : 's'}
              {aiResult.types.length ? ` · ${aiResult.types.join(', ')}` : ''} ·
              machine-proposed, status=draft
            </Typography>
          )}
        </Stack>
        {aiResult && aiResult.features === 0 && aiResult.message && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
            {aiResult.message}
          </Typography>
        )}
        {aiError && (
          <Typography variant="caption" color="error" sx={{ mt: 0.5 }}>
            {aiError}
          </Typography>
        )}
      </Paper>

      <SliderField
        label="Rainfall"
        unit="mm"
        min={0}
        max={800}
        value={Number(scene?.envParams.precipitation ?? 0)}
        onChange={(precipitation) => setEnvParams({ precipitation })}
      />
      <SliderField
        label="Temperature"
        unit="°C"
        min={10}
        max={45}
        value={Number(scene?.envParams.temperature ?? 25)}
        onChange={(temperature) => setEnvParams({ temperature })}
      />

      {/* What the current plan costs, in the units the brief asks for. Read-only;
          the four reporting endpoints behind it issue no edits. */}
      <ImpactPanel sceneId={sceneId} />
    </Box>
  )
}
