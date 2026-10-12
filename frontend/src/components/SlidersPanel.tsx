/**
 * Top-right scenario panel: the two run sliders and the Run button.
 *
 * Pulled out of the old `ScenePanel` for the layout reorg. The sliders write to
 * per-run local state (ADR-024: never persisted onto the scene); Run sends them
 * and the active plan to the live simulation endpoint.
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
  const activePlanId = usePlanStore((s) => s.activePlanId)

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
          min={10}
          max={45}
          value={Number(scene?.envParams.temperature ?? 25)}
          onChange={(temperature) => setEnvParams({ temperature })}
        />

        <Button
          variant="contained"
          fullWidth
          disabled={!scene || running}
          onClick={() => void run(activePlanId)}
          sx={{ mt: 0.5 }}
        >
          {running ? 'Running…' : '▶ Run Simulation'}
        </Button>

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
