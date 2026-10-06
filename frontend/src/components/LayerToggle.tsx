/**
 * Layer switch (spec §1.1 `components/LayerToggle.tsx`, item C7).
 *
 * Continuous fields and discrete geometry are switched here but rendered by two
 * different code paths (ADR-032), so a disabled row explains itself.
 */
import Box from '@mui/material/Box'
import FormControlLabel from '@mui/material/FormControlLabel'
import Switch from '@mui/material/Switch'
import Typography from '@mui/material/Typography'

import type { LayerVisibility } from '@/api/types'
import { useSceneStore } from '@/state/sceneStore'

export default function LayerToggle() {
  const layers = useSceneStore((s) => s.layers)
  const toggleLayer = useSceneStore((s) => s.toggleLayer)
  const terrain = useSceneStore((s) => s.terrain)

  const rows: Array<[keyof LayerVisibility, string, boolean]> = [
    ['imagery', 'Base imagery', true],
    ['boundary', 'Study-area boundary', true],
    ['wetGrid', 'Wet grid (raster)', Boolean(terrain?.wetGridUrl)],
    ['heatmap', 'Heat exposure (raster)', Boolean(terrain?.heatmapUrl)],
  ]

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
    </Box>
  )
}
