/**
 * Intervention legend — 2.5D block colour + extrusion height, one row per kind.
 *
 * The scene renders each intervention as a coloured, extruded block. Without a
 * legend the mapping is "trust the shade of blue"; the legend makes it a
 * reading. Entries mirror `STYLE_FOR_KIND` in `backend/app/services/ai_plan.py`
 * — if you change one, change the other, there is no runtime check.
 *
 * A 2.5D block is a **depiction, not a measurement** (DESIGN §2.4): the
 * extrusion is a legibility device, so each row says what its height *means*
 * ("14 m · structure", "6 m · canopy", "1.5 m · depression") rather than
 * implying a surveyed figure. While a confirm gate is open the legend also
 * gains a `Draft (unconfirmed)` row, so the translucent dashed blocks on screen
 * are named rather than left to be mistaken for placed features.
 *
 * Hidden until the active plan has at least one intervention, or a proposal
 * preview is on screen: empty chrome explaining what isn't drawn is noise.
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
  /** What the extrusion depicts — a structure, a canopy, a depression, a surface. */
  extrusionMeaning: string
}

const ENTRIES: LegendEntry[] = [
  { kind: 'water_reservoir', label: 'Reservoir', color: '#1f6feb', heightM: 14, extrusionMeaning: 'structure' },
  { kind: 'green_space', label: 'Green space', color: '#2ea043', heightM: 6, extrusionMeaning: 'canopy' },
  { kind: 'permeable_paving', label: 'Permeable paving', color: '#8957e5', heightM: 2, extrusionMeaning: 'surface' },
  { kind: 'drainage', label: 'Drainage', color: '#e3b341', heightM: 3, extrusionMeaning: 'structure' },
  { kind: 'road', label: 'Road', color: '#6e7681', heightM: 2, extrusionMeaning: 'surface' },
]

export default function InterventionLegend() {
  const show = useSceneStore((s) => s.layers.interventions)
  const features = usePlanStore((s) => s.interventions.features)
  const proposals = usePlanStore((s) => s.proposals)

  const showPlaced = show && features.length > 0
  const showDraft = proposals.length > 0
  if (!showPlaced && !showDraft) return null

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
              {e.heightM} m · {e.extrusionMeaning}
            </Typography>
          </Stack>
        ))}
        {showDraft && (
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: '2px',
                flexShrink: 0,
                border: '1px dashed',
                borderColor: 'text.secondary',
                backgroundColor: 'rgba(255,255,255,0.08)',
              }}
            />
            <Typography
              sx={{ fontSize: 10, lineHeight: 1.25, flexGrow: 1, color: 'text.primary' }}
            >
              Draft (unconfirmed)
            </Typography>
            <Typography
              className="hud-mono"
              sx={{ fontSize: 9, lineHeight: 1.25, color: 'text.secondary' }}
            >
              40% · dashed
            </Typography>
          </Stack>
        )}
      </Stack>
    </Box>
  )
}
