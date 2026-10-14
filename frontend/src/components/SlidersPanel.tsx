/**
 * Top-right scenario panel: the two run sliders and the Run button.
 *
 * The sliders write to per-run local state (ADR-024: never persisted onto the
 * scene); Run sends them and the **whole project selection** to the live
 * simulation endpoint, so a run scores every active project together.
 */
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import SliderField from './SliderField'

export default function SlidersPanel() {
  const scene = useSceneStore((s) => s.scene)
  const setEnvParams = useSceneStore((s) => s.setEnvParams)
  const run = useSceneStore((s) => s.run)
  const running = useSceneStore((s) => s.running)
  const runError = useSceneStore((s) => s.runError)
  // Every project selected in the catalog, not just the highlighted one: the
  // brief's goal is cumulative effect, and the backend composes this list into
  // one scenario.
  const selectedPlanIds = usePlanStore((s) => s.selectedPlanIds)

  // Rainfall is intensity (mm/hr) × duration (hr) → storm-total depth, which is
  // what SCS-CN consumes. The presets are the HKO rainstorm-signal thresholds,
  // which are hourly rates — so they now set *intensity* and are finally honest.
  const intensity = Number(scene?.envParams.precipitation ?? 50)
  const durationH = Number(scene?.envParams.durationHours ?? 1)
  const totalMm = intensity * durationH
  const tideState = (scene?.envParams.tideState ?? 'low') as
    'low' | 'mean' | 'high'

  const RAIN_SIGNALS = [
    { label: 'Amber', value: 30, color: '#f5a623' },
    { label: 'Red', value: 50, color: '#ff6b6b' },
    { label: 'Black', value: 70, color: '#9aa0a6' },
  ] as const

  const TIDE_OPTIONS = [
    { label: 'Low', value: 'low', color: '#2f9e44' },
    { label: 'Mean', value: 'mean', color: '#f5a623' },
    { label: 'High', value: 'high', color: '#1c7ed6' },
  ] as const

  // Temperature: the scenario mean surface temperature (C). The whole field
  // re-anchors so its mean matches. The buttons mirror the HKO warning system,
  // the same way the rainfall buttons mirror the rainstorm signals:
  //   Hot night         — daily min >= 28 C          (HKO "hot night")
  //   Very Hot Warning  — daily max >= 33 C          (HKO Very Hot Weather Warning)
  //   Extreme heat      — "extremely hot weather" special alert, max >= 35 C
  // These are *air* temperatures; air ~= surface - 12 C, so each button sets
  // the equivalent surface mean so it stays honest to the surface slider.
  const temperatureC = Number(scene?.envParams.temperature ?? 30)
  const TEMP_PRESETS = [
    { label: 'Hot night', value: 40, airC: 28, color: '#f5a623', onColor: '#1a1a1a' },
    { label: 'Very hot', value: 45, airC: 33, color: '#ff6b6b', onColor: '#1a1a1a' },
    { label: 'Extreme', value: 47, airC: 35, color: '#9aa0a6', onColor: '#1a1a1a' },
  ] as const
  const activePreset = TEMP_PRESETS.find((p) => p.value === temperatureC)

  return (
    <HudPanel title="Scenario Inputs">
      <Stack spacing={1.5}>
        <SliderField
          label="Rainfall intensity"
          unit="mm/hr"
          min={0}
          max={200}
          value={intensity}
          marks={[
            { value: 30, label: '30' },
            { value: 50, label: '50' },
            { value: 70, label: '70' },
          ]}
          caption={`Amber 30 · Red 50 · Black 70 mm/hr. At ${intensity} mm/hr × ${durationH} h = ${totalMm.toFixed(0)} mm total.`}
          onChange={(precipitation) => setEnvParams({ precipitation })}
        />

        <Box>
          <Typography className="hud-label" sx={{ fontSize: 8.5, mb: 0.25 }}>
            Rainstorm signal
          </Typography>
          <Stack direction="row" spacing={0.5}>
            {RAIN_SIGNALS.map((sig) => (
              <Button
                key={sig.label}
                size="small"
                variant="outlined"
                onClick={() => setEnvParams({ precipitation: sig.value })}
                sx={{
                  flex: 1,
                  minWidth: 0,
                  px: 0.5,
                  py: 0.25,
                  fontSize: 10,
                  lineHeight: 1.4,
                  borderColor: sig.color,
                  color: sig.color,
                  textTransform: 'none',
                  whiteSpace: 'normal',
                  '&:hover': {
                    borderColor: sig.color,
                    bgcolor: `${sig.color}1a`,
                  },
                }}
              >
                {sig.label}
              </Button>
            ))}
          </Stack>
        </Box>

        <SliderField
          label="Duration"
          unit="hr"
          min={0.5}
          max={24}
          step={0.5}
          value={durationH}
          marks={[
            { value: 1, label: '1' },
            { value: 3, label: '3' },
            { value: 6, label: '6' },
          ]}
          caption="Storm length. Intensity × duration = total depth for runoff."
          onChange={(durationHours) => setEnvParams({ durationHours })}
        />

        <Box>
          <Typography className="hud-label" sx={{ fontSize: 8.5, mb: 0.25 }}>
            Tide state
          </Typography>
          <Stack direction="row" spacing={0.5}>
            {TIDE_OPTIONS.map((t) => (
              <Button
                key={t.value}
                size="small"
                variant={tideState === t.value ? 'contained' : 'outlined'}
                onClick={() => setEnvParams({ tideState: t.value })}
                sx={{
                  flex: 1,
                  minWidth: 0,
                  px: 0.5,
                  py: 0.25,
                  fontSize: 10,
                  lineHeight: 1.4,
                  textTransform: 'none',
                  whiteSpace: 'nowrap',
                  ...(tideState !== t.value && {
                    borderColor: t.color,
                    color: t.color,
                    '&:hover': { borderColor: t.color, bgcolor: `${t.color}1a` },
                  }),
                }}
              >
                {t.label}
              </Button>
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: 8.5, display: 'block', mt: 0.25 }}>
            Screening backwater — higher tide keeps water on the grid.
          </Typography>
        </Box>

        <SliderField
          label="Surface temperature"
          unit="°C"
          min={24}
          max={50}
          value={temperatureC}
          marks={[{ value: 41, label: '41°' }]}
          caption="Re-anchors the heatmap to this mean; shade, green and wind shelter still decide which cells run hotter or cooler."
          onChange={(temperature) => setEnvParams({ temperature })}
        />

        <Box>
          <Typography className="hud-label" sx={{ fontSize: 8.5, mb: 0.25 }}>
            Heat warning (HKO)
          </Typography>
          <Stack direction="row" spacing={0.5}>
            {TEMP_PRESETS.map((p) => (
              <Button
                key={p.label}
                size="small"
                variant={activePreset?.value === p.value ? 'contained' : 'outlined'}
                onClick={() => setEnvParams({ temperature: p.value })}
                sx={{
                  flex: 1,
                  minWidth: 0,
                  px: 0.5,
                  py: 0.25,
                  fontSize: 10,
                  lineHeight: 1.4,
                  textTransform: 'none',
                  whiteSpace: 'normal',
                  ...(activePreset?.value === p.value
                    ? { bgcolor: p.color, color: p.onColor, '&:hover': { bgcolor: p.color } }
                    : {
                        borderColor: p.color,
                        color: p.color,
                        '&:hover': { borderColor: p.color, bgcolor: `${p.color}1a` },
                      }),
                }}
              >
                {p.label} {p.airC}°
              </Button>
            ))}
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: 8.5, display: 'block', mt: 0.25 }}>
            Air temp (≥28 / ≥33 / ≥35 °C) → surface − 12 °C. Rendered as deviation from the mean.
          </Typography>
        </Box>

        <Button
          variant="contained"
          fullWidth
          disabled={!scene || running}
          onClick={() => void run(selectedPlanIds)}
          sx={{ mt: 0.5 }}
        >
          {running ? (
            <Stack direction="row" spacing={0.75} alignItems="center" justifyContent="center">
              {/* The button is filled with the signal green, so a shimmering label
                  would fight its own background. A small pulsing ring reads as
                  "working" without touching the text. */}
              <Box
                className="hud-spinner"
                component="span"
                sx={{ width: 11, height: 11, borderRadius: '50%', border: '2px solid rgba(4,18,10,0.35)', borderTopColor: '#04120a' }}
              />
              <span>Running…</span>
            </Stack>
          ) : selectedPlanIds.length > 1 ? (
            `▶ Run ${selectedPlanIds.length} projects`
          ) : (
            '▶ Run Simulation'
          )}
        </Button>

        {selectedPlanIds.length === 0 && !running && (
          <Typography variant="caption" color="text.secondary">
            No projects selected — this scores today's land cover.
          </Typography>
        )}
        {running && (
          <Typography className="hud-shimmer" variant="caption" sx={{ display: 'block' }}>
            Re-running runoff + retention at{' '}
            {intensity.toFixed(0)} mm/hr × {durationH} h ({totalMm.toFixed(0)} mm
            total) at {tideState} tide and re-rendering both overlays.
          </Typography>
        )}
        {runError && <Alert severity="error" variant="outlined">{runError}</Alert>}
      </Stack>
    </HudPanel>
  )
}
