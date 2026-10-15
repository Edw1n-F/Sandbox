/**
 * Intervention legend — 2.5D block colour + extrusion height, one row per kind.
 *
 * The scene renders each intervention as a coloured, extruded block. Without a
 * legend the mapping is "trust the shade of blue"; the legend makes it a
 * reading. Entries mirror `STYLE_FOR_KIND` in `backend/app/services/ai_plan.py`
 * — if you change one, change the other, there is no runtime check.
 *
 * Hidden until the active plan has at least one intervention: an empty baseline
 * doesn't need chrome explaining what isn't drawn.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

interface LegendEntry {
  kind: string
  label: string
  color: string
  heightM: number
}

const ENTRIES: LegendEntry[] = [
  { kind: 'water_reservoir', label: 'Reservoir', color: '#1f6feb', heightM: 14 },
  { kind: 'green_space', label: 'Green space', color: '#2ea043', heightM: 6 },
  { kind: 'permeable_paving', label: 'Permeable paving', color: '#8957e5', heightM: 2 },
  { kind: 'drainage', label: 'Drainage', color: '#e3b341', heightM: 3 },
  { kind: 'road', label: 'Road', color: '#6e7681', heightM: 2 },
]

export default function InterventionLegend() {
  const show = useSceneStore((s) => s.layers.interventions)
  const features = usePlanStore((s) => s.interventions.features)

  if (!show || features.length === 0) return null

  return (
    <Box
      sx={{
        position: 'absolute',
        left: 12,
        bottom: 16,
        zIndex: 1200,
        px: 1.25,
        py: 1,
        minWidth: 160,
        border: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(8, 13, 12, 0.86)',
        backdropFilter: 'blur(12px)',
        borderRadius: '10px',
        pointerEvents: 'none',
      }}
    >
      <Typography
        className="hud-label"
        sx={{ fontSize: 9, lineHeight: 1.3, mb: 0.5, letterSpacing: 0.5 }}
      >
        INTERVENTIONS
      </Typography>
      <Stack spacing={0.4}>
        {ENTRIES.map((e) => (
          <Stack key={e.kind} direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: '2px',
                backgroundColor: e.color,
                flexShrink: 0,
              }}
            />
            <Typography
              sx={{ fontSize: 10, lineHeight: 1.25, flexGrow: 1, color: 'text.primary' }}
            >
              {e.label}
            </Typography>
            <Typography
              className="hud-mono"
              sx={{ fontSize: 9, lineHeight: 1.25, color: 'text.secondary' }}
            >
              {e.heightM} m
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  )
}
