/**
 * Overlay toggles: the wet grid and the heat map.
 *
 * Same card as the other right-column panels (HudPanel). Both overlays appear
 * after a Run; until then the switches are disabled and say why, instead of
 * flipping a layer that has nothing to show.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Switch from '@mui/material/Switch'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import { useSceneStore } from '@/state/sceneStore'

type OverlayKey = 'wetGrid' | 'heatmap'

const ROWS: Array<{ key: OverlayKey; label: string; swatch: string; hint: string }> = [
  { key: 'wetGrid', label: 'Wet grid', swatch: '#2f9bff', hint: 'where unstored water collects' },
  { key: 'heatmap', label: 'Heat map', swatch: '#ff5a3c', hint: 'relative heat exposure' },
]

export default function LayerPanel() {
  const layers = useSceneStore((s) => s.layers)
  const toggleLayer = useSceneStore((s) => s.toggleLayer)
  const terrain = useSceneStore((s) => s.terrain)
  const runResult = useSceneStore((s) => s.runResult)

  const urls: Record<OverlayKey, string | null | undefined> = {
    wetGrid: terrain?.wetGridUrl,
    heatmap: terrain?.heatmapUrl,
  }
  const hasAny = Boolean(urls.wetGrid || urls.heatmap)

  return (
    <HudPanel
      title="Layers"
      right={
        !runResult && hasAny ? (
          <Typography variant="caption" color="text.secondary">
            baseline
          </Typography>
        ) : undefined
      }
    >
      <Stack spacing={0.5}>
        {ROWS.map(({ key, label, swatch, hint }) => {
          const available = Boolean(urls[key])
          return (
            <Stack
              key={key}
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{ opacity: available ? 1 : 0.45 }}
            >
              <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0 }}>
                <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: swatch, flexShrink: 0 }} />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" sx={{ lineHeight: 1.2 }}>
                    {label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1.1 }}>
                    {available ? hint : 'run a simulation first'}
                  </Typography>
                </Box>
              </Stack>
              <Switch
                size="small"
                checked={layers[key] && available}
                disabled={!available}
                onChange={() => toggleLayer(key)}
                inputProps={{ 'aria-label': `Show ${label.toLowerCase()}` }}
              />
            </Stack>
          )
        })}
      </Stack>
    </HudPanel>
  )
}
