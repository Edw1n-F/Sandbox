/**
 * Scene parameter panel (spec §1.1 `components/ScenePanel.tsx`, item C6).
 *
 * Status plus the rainfall / temperature sliders. Per CLAUDE.md this layer only
 * "triggers state changes, no rendering logic" -- it never renders the map.
 */
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

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
      <Typography variant="caption" color="text.secondary">
        {plan?.label ?? 'No plan'} · {features} feature{features === 1 ? '' : 's'}
      </Typography>

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
