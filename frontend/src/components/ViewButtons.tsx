/**
 * Bottom-centre view-control pill.
 *
 * Tilt is now a gesture (right-drag or two-finger trackpad drag — see
 * `cameraRig.bindOrbitGestures`), so the pill carries only the controls a
 * gesture cannot do: orbit left/right, zoom in/out, jump to top-down, and
 * re-frame. Fewer buttons, less redundancy, no tilt-vs-drag conflict.
 */
import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'

import { cameraControl } from '@/view/SandboxViewer'

/** One rounded icon button. Unicode glyphs keep the bundle free of icon deps. */
function Btn({
  label,
  glyph,
  onClick,
}: {
  label: string
  glyph: string
  onClick: () => void
}) {
  return (
    <IconButton
      aria-label={label}
      title={label}
      onClick={onClick}
      size="small"
      sx={{
        width: 32,
        height: 32,
        borderRadius: '6px',
        color: '#dce8e2',
        '&:hover': { backgroundColor: 'rgba(92,255,157,0.12)', color: '#5cff9d' },
      }}
    >
      <span style={{ fontSize: 16, lineHeight: 1 }}>{glyph}</span>
    </IconButton>
  )
}

export default function ViewButtons() {
  return (
    <Box
      sx={{
        position: 'absolute',
        bottom: 22,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1200,
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        px: 1,
        py: 0.5,
        borderRadius: '10px',
        border: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(8, 13, 12, 0.88)',
        backdropFilter: 'blur(16px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
      }}
    >
      <Btn label="Orbit left" glyph="⟲" onClick={() => cameraControl.orbit(-24, 0)} />
      <Btn label="Orbit right" glyph="⟳" onClick={() => cameraControl.orbit(24, 0)} />
      <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
      <Btn label="Zoom in" glyph="＋" onClick={() => cameraControl.zoom(0.55)} />
      <Btn label="Zoom out" glyph="－" onClick={() => cameraControl.zoom(1.8)} />
      <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
      <Btn label="Top-down (read rasters)" glyph="▭" onClick={() => cameraControl.topDown()} />
      <Btn label="Reset framing" glyph="⌂" onClick={() => cameraControl.reset()} />
    </Box>
  )
}
