/**
 * Layer switch panel — controls visibility of all overlay layers.
 *
 * Rows are shown as disabled when the underlying data is not present,
 * so the switch is never silently broken.
 */
import Box from '@mui/material/Box'
import FormControlLabel from '@mui/material/FormControlLabel'
import Switch from '@mui/material/Switch'
import Typography from '@mui/material/Typography'

import type { LayerVisibility } from '@/api/types'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import { onlinePropStatus, usePropsRevision, useOverlayStatus } from '@/view/SandboxViewer'

export default function LayerToggle() {
  const layers = useSceneStore((s) => s.layers)
  const toggleLayer = useSceneStore((s) => s.toggleLayer)
  const terrain = useSceneStore((s) => s.terrain)
  usePropsRevision()
  const propsStatus = onlinePropStatus()
  const status = useOverlayStatus()
  const interventionCount = usePlanStore((s) => s.interventions.features.length)
  const interventionLabel =
    interventionCount > 0
      ? `Interventions (${interventionCount})`
      : 'Interventions (none in plan)'

  const rows: Array<[keyof LayerVisibility, string, boolean]> = [
    ['imagery', 'Base imagery', true],
    ['boundary', 'Study-area boundary', true],
    ['wetGrid', 'Wet grid (raster)', Boolean(terrain?.wetGridUrl)],
    ['heatmap', 'Heat exposure (raster)', Boolean(terrain?.heatmapUrl)],
    ['interventions', interventionLabel, interventionCount > 0],
    ['cityModel', '3D massing model', true],
    ['realBuildings', 'Real 3D buildings (Lands Dept)', Boolean(terrain?.cityModel3DUrl ?? terrain?.cityModel3dUrl)],
    ['props', `Online 3D assets (${propsStatus.loaded})`, true],
    ['waterbody', 'Water bodies (Lands Dept)', Boolean(terrain?.waterbodyUrl)],
    ['infrastructure', 'Infrastructure (Lands Dept)', Boolean(terrain?.infrastructureUrl)],
  ]

  const failed = status.wetGrid === 'failed' || status.heatmap === 'failed'

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="overline" color="text.secondary">
        Layers
      </Typography>
      {rows.map(([key, label, available]) => (
        <FormControlLabel
          key={key}
          control={
            <Switch
              size="small"
              checked={layers[key]}
              disabled={!available}
              onChange={() => toggleLayer(key)}
            />
          }
          label={label}
        />
      ))}
      {failed && (
        <Typography variant="caption" color="error" sx={{ mt: 0.5 }}>
          An overlay failed to load — see the browser console.
        </Typography>
      )}
      {!terrain?.wetGridUrl && !terrain?.heatmapUrl && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
          Overlays are off. Generate them with the scripts in backend/scripts,
          then reload the scene.
        </Typography>
      )}
    </Box>
  )
}
