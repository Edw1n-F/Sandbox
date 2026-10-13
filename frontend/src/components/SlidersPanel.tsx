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

  const RAIN_SIGNALS = [
    { label: 'Amber', value: 30, color: '#f5a623' },
    { label: 'Red', value: 50, color: '#d32f2f' },
    { label: 'Black', value: 70, color: '#1a1a1a' },
  ] as const

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
          caption={`HKO rainstorm signals are hourly rates: Amber 30, Red 50, Black 70 mm/hr.`
            + ` At ${intensity} mm/hr × ${durationH} h this event totals ${totalMm.toFixed(0)} mm.`}
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
                  whiteSpace: 'nowrap',
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
          caption="How long the storm lasts. Intensity × duration = storm-total depth fed to the SCS-CN runoff equation."
          onChange={(durationHours) => setEnvParams({ durationHours })}
        />

        <SliderField
          label="Temperature"
          unit="°C"
          min={24}
          max={42}
          value={Number(scene?.envParams.temperature ?? 30)}
          marks={[{ value: 35, label: '35°' }]}
          caption="Stretches the heat colour scale above 35 °C. Does not change rainfall or runoff."
          onChange={(temperature) => setEnvParams({ temperature })}
        />

        <Button
          variant="contained"
          fullWidth
          disabled={!scene || running}
          onClick={() => void run(selectedPlanIds)}
          sx={{ mt: 0.5 }}
        >
          {running
            ? 'Running…'
            : selectedPlanIds.length > 1
              ? `▶ Run ${selectedPlanIds.length} projects`
              : '▶ Run Simulation'}
        </Button>

        {selectedPlanIds.length === 0 && !running && (
          <Typography variant="caption" color="text.secondary">
            No projects selected — this scores today's land cover.
          </Typography>
        )}
        {running && (
          <Typography variant="caption" color="text.secondary">
            Re-running runoff + retention at{' '}
            {intensity.toFixed(0)} mm/hr × {durationH} h ({totalMm.toFixed(0)} mm
            total) and re-rendering both overlays.
          </Typography>
        )}
        {runError && <Alert severity="error" variant="outlined">{runError}</Alert>}
      </Stack>
    </HudPanel>
  )
}
