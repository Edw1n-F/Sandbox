/**
 * Top-right scenario panel: the two run sliders and the Run button.
 *
 * The sliders write to per-run local state (ADR-024: never persisted onto the
 * scene); Run sends them and the **whole project selection** to the live
 * simulation endpoint, so a run scores every active project together.
 */
import Alert from '@mui/material/Alert'
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

  return (
    <HudPanel title="Scenario Inputs">
      <Stack spacing={1.5}>
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
            {Number(scene?.envParams.precipitation ?? 450).toFixed(0)} mm and
            re-rendering both overlays.
          </Typography>
        )}
        {runError && <Alert severity="error" variant="outlined">{runError}</Alert>}
      </Stack>
    </HudPanel>
  )
}
