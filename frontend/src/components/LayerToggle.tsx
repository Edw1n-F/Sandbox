/**
 * Layer switch (spec §1.1 `components/LayerToggle.tsx`, item C7).
 *
 * Continuous fields and discrete geometry are switched here but rendered by two
 * different code paths (ADR-032), so a disabled row explains itself.
 *
 * A failed overlay is reported in the panel rather than left as a switch that
 * silently does nothing — a Cesium imagery layer that fails to load is
 * otherwise indistinguishable from one that was never switched on.
 */
import Box from '@mui/material/Box'
import FormControlLabel from '@mui/material/FormControlLabel'
import Switch from '@mui/material/Switch'
import Typography from '@mui/material/Typography'

import type { LayerVisibility } from '@/api/types'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'
import { onlinePropStatus, usePropsRevision } from '@/view/SandboxViewer'
import { useOverlayStatus } from '@/view/SandboxViewer'

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
    // Interventions are geometry rather than a raster, but they belong in the
    // same list: it is the user's single "what am I looking at" control.
    ['interventions', interventionLabel, interventionCount > 0],
    // The 3D massing model. Disabled only when the script has not been run.
    ['cityModel', '3D city model (massing)', Boolean(terrain?.cityModelUrl)],
    // Online assets always resolve, so this row is never disabled.
    ['props', `Online 3D assets (${propsStatus.loaded})`, true],
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
