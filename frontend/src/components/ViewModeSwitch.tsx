/**
 * View-mode switch, plus the tactical legend.
 *
 * ## Why the legend lives here
 *
 * The tactical mode encodes envelope height as colour. A colour encoding with no
 * key is decoration, not information, so the key ships with the switch instead
 * of in a help panel nobody opens.
 *
 * ## Why the provisional note is in both modes
 *
 * A darker, more dramatic renderer makes a model look more authoritative than it
 * is. The tactical mode changes how the same provisional massing envelopes are
 * drawn; it does not make them surveyed buildings, and the copy must not let a
 * screenshot imply otherwise. So the caveat is repeated here rather than left to
 * the city-model card further down the panel.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Typography from '@mui/material/Typography'

import type { ViewMode } from '@/api/types'
import { useSceneStore } from '@/state/sceneStore'
import { GRID_SPACING_M, HEIGHT_RAMP, VIEW_MODES } from '@/view/viewModes'

/** Outline colours, matching `TECH_STATES` in `techMaterial.ts`. */
const STATE_SWATCHES: Array<[string, string]> = [
  ['#3fe0ff', 'as generated'],
  ['#ff9d2e', 'edited by a plan'],
  ['#5cff9d', 'added by a plan'],
  ['#ffffff', 'selected'],
]

export default function ViewModeSwitch() {
  const viewMode = useSceneStore((s) => s.viewMode)
  const setViewMode = useSceneStore((s) => s.setViewMode)
  const terrain = useSceneStore((s) => s.terrain)
  const heightMeta = terrain?.cityModelMeta?.heightM
  const tactical = viewMode === 'tactical'

  return (
    <Box sx={{ p: 2, pt: 0 }}>
      <Typography variant="overline" color="text.secondary">
        View
      </Typography>

      <ToggleButtonGroup
        exclusive
        fullWidth
        size="small"
        value={viewMode}
        onChange={(_event, next: ViewMode | null) => {
          // `null` arrives when the active button is clicked again. Snapping back
          // is the right reading of that: there is no "no view mode".
          if (next) setViewMode(next)
        }}
      >
        {(['survey', 'tactical'] as ViewMode[]).map((mode) => (
          <ToggleButton key={mode} value={mode} sx={{ flex: 1 }}>
            {VIEW_MODES[mode].label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <Typography
        variant="caption"
        color="text.secondary"
        display="block"
        sx={{ mt: 0.5 }}
      >
        {VIEW_MODES[viewMode].blurb}
      </Typography>

      {tactical && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary" display="block">
            Envelope height — {HEIGHT_RAMP.bands} bands
            {heightMeta
              ? `, ${heightMeta.min.toFixed(0)}–${heightMeta.max.toFixed(0)} m`
              : ''}
          </Typography>
          <Box
            sx={{
              height: 8,
              borderRadius: 1,
              mt: 0.5,
              mb: 0.25,
              background: `linear-gradient(to right, ${HEIGHT_RAMP.low}, ${HEIGHT_RAMP.high})`,
            }}
          />
          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption" color="text.secondary">
              low
            </Typography>
            <Typography variant="caption" color="text.secondary">
              high
            </Typography>
          </Stack>

          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
            Outline
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mt: 0.25 }}>
            {STATE_SWATCHES.map(([hex, label]) => (
              <Stack key={hex} direction="row" spacing={0.5} alignItems="center">
                <Box
                  sx={{
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: hex,
                    boxShadow: `0 0 6px ${hex}`,
                  }}
                />
                <Typography variant="caption" color="text.secondary">
                  {label}
                </Typography>
              </Stack>
            ))}
          </Box>

          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
            Ground grid {GRID_SPACING_M} m. Envelopes remain provisional massing,
            not surveyed buildings — this is a different renderer, not new data.
          </Typography>
        </Box>
      )}
    </Box>
  )
}
