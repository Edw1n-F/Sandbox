/**
 * Scene parameter panel (spec §1.1 `components/ScenePanel.tsx`, item C6).
 *
 * Status plus the rainfall / temperature sliders. Per CLAUDE.md this layer only
 * "triggers state changes, no rendering logic" -- it never renders the map.
 *
 * The plain-language intervention input lives in `PromptBar`, floating over the
 * 3D view; everything here is read-side reporting and direct controls.
 */
import Alert from '@mui/material/Alert'
import Button from '@mui/material/Button'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import MenuItem from '@mui/material/MenuItem'
import Select, { type SelectChangeEvent } from '@mui/material/Select'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { useEffect, useState } from 'react'

import HudPanel from '@/components/HudPanel'
import ImpactPanel from '@/components/ImpactPanel'
import RunSummary from '@/components/RunSummary'
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
  const sceneId = useSceneStore((s) => s.sceneId)
  const scene = useSceneStore((s) => s.scene)
  const error = useSceneStore((s) => s.error)
  const setEnvParams = useSceneStore((s) => s.setEnvParams)
  const run = useSceneStore((s) => s.run)
  const running = useSceneStore((s) => s.running)
  const runResult = useSceneStore((s) => s.runResult)
  const runError = useSceneStore((s) => s.runError)
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
          targetLon?: number
          targetLat?: number
          widthScale?: number
          depthScale?: number
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
        // Direct coordinate move if targetLon/Lat are present.
        if (typeof p.dxM === 'undefined' && typeof p.dyM === 'undefined'
            && p.rationale?.includes('coordinate')) {
          what = 'reposition to coordinate'
        } else {
          const dx = Number(p.dxM ?? 0)
          const dy = Number(p.dyM ?? 0)
          const ns = dy > 0 ? 'north' : dy < 0 ? 'south' : ''
          const ew = dx > 0 ? 'east' : dx < 0 ? 'west' : ''
          const bearing = [ns, ew].filter(Boolean).join('-') || '—'
          const distance = Math.round(Math.hypot(dx, dy))
          what = `move ${distance} m ${bearing}`
        }
        break
      }
      case 'resize': {
        const ws = p.widthScale ?? 1
        const ds = p.depthScale ?? 1
        const parts: string[] = []
        if (ws !== 1) parts.push(`width ×${ws}`)
        if (ds !== 1) parts.push(`depth ×${ds}`)
        what = `resize ${parts.join(', ')}`
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
  const loading = usePlanStore((s) => s.loading)
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

  return (
    <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box>
        <Typography className="hud-label">Scene</Typography>
        <Typography variant="h6" sx={{ lineHeight: 1.2, mt: 0.25 }}>
          {scene?.name ?? 'No scene'}
        </Typography>
        {scene && (
          <Typography className="hud-mono" sx={{ fontSize: 10, color: 'text.secondary', mt: 0.25 }}>
            {scene.center.coordinates[1].toFixed(4)}°N · {scene.center.coordinates[0].toFixed(4)}°E
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
        <Typography className="hud-label">Plan</Typography>
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
        <HudPanel
          title="3D City Model"
          right={<Chip size="small" color="warning" label="provisional" />}
        >
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
              −20%
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
        </HudPanel>
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

      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
        <Typography className="hud-label" sx={{ flex: 1 }}>
          Scenario
        </Typography>
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
          RUN INPUTS — NOT PERSISTED (ADR-024)
        </Typography>
      </Stack>

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

      {/* The trigger the two sliders above were waiting for. Until this existed
          the overlays were static PNGs rendered offline and the sliders wrote
          nowhere (ADR-024), so the panel described a simulation it could not
          run. The plan sent is the one the panel is showing, so what the
          planner sees listed is what gets scored. */}
      <Button
        variant="contained"
        size="large"
        fullWidth
        disabled={!sceneId || running}
        onClick={() => void run(activePlanId)}
      >
        {running ? 'Running…' : '▶ Run simulation'}
      </Button>
      {running && (
        <Typography variant="caption" color="text.secondary">
          Re-running the runoff and retention arithmetic at{' '}
          {Number(scene?.envParams.precipitation ?? 450).toFixed(0)} mm and
          re-rendering both overlays.
        </Typography>
      )}
      {runError && (
        <Alert severity="error" variant="outlined">{runError}</Alert>
      )}
      {runResult && !running && <RunSummary result={runResult} />}

      {/* What the current plan costs, in the units the brief asks for. Read-only;
          the four reporting endpoints behind it issue no edits. */}
      <ImpactPanel sceneId={sceneId} />
    </Box>
  )
}
