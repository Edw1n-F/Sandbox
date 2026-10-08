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
import {
  cityModelStats,
  editCityModelHeights,
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
  // same posture ADR-030 requires: named, attributed, and reversible.
  const interventionFeatures = usePlanStore((s) => s.interventions.features)
  const massingEdit = interventionFeatures.find(
    (f) => f.properties?.interventionType === 'massing_height',
  )
  const massingParams = massingEdit?.properties?.params as
    | { op?: string; multiplier?: number; heightM?: number; rationale?: string }
    | undefined
  const massingSummary = massingParams
    ? massingParams.op === 'reset'
      ? 'reset to generated heights'
      : massingParams.op === 'scale_all'
        ? `scale all x${massingParams.multiplier}`
        : `set ${massingParams.heightM} m`
    : null
  const selectPlan = usePlanStore((s) => s.selectPlan)
  const refreshPlans = usePlanStore((s) => s.refreshPlans)
  const sceneId = useSceneStore((s) => s.sceneId)
  const [instruction, setInstruction] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [aiResult, setAiResult] = useState<AiPlanResult | null>(null)
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
            {city.selected ? ` · selected ${city.selected}` : ''}
          </Typography>

          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
            {terrain?.cityModelMeta?.note ?? 'Massing derived from land cover.'}
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
              onClick={() => { editCityModelHeights(1); selectCityBuilding(null); setCity(cityModelStats()) }}>
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

      {massingSummary && (
        <Alert severity="info" variant="outlined" sx={{ py: 0 }}>
          <Typography variant="caption">
            <strong>AI massing edit:</strong> {massingSummary}
            {massingParams?.rationale ? ` — ${massingParams.rationale}` : ''}
            . Applied to the live model; switch plans to revert.
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
          placeholder="e.g. add two water reservoirs in the deepest basins and green space on the largest open blocks"
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
          <Typography variant="caption" color="text.secondary">
            {aiResult
              ? `${aiResult.provider} · ${aiResult.features} feature${
                  aiResult.features === 1 ? '' : 's'
                }${aiResult.types.length ? ` · ${aiResult.types.join(', ')}` : ''}`
              : 'Machine-proposed, status=draft'}
          </Typography>
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
    </Box>
  )
}
