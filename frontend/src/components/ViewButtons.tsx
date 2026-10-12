/**
 * Bottom-centre view-control pill.
 *
 * The camera controls that used to live in the left drawer, floated over the
 * map as a single rounded pill — the reference film's "instruments on the map"
 * posture. Replaces both the old `CameraController` and the prompt pill that
 * sat here before the reorg (the intervention input moved into `ChatPanel`).
 */
import Box from '@mui/material/Box'
import IconButton from '@mui/material/IconButton'
import Tooltip from '@mui/material/Tooltip'

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
    <Tooltip title={label} arrow>
      <IconButton
        aria-label={label}
        onClick={onClick}
        size="small"
        sx={{
          width: 32,
          height: 32,
          borderRadius: '8px',
          color: '#dce8e2',
          '&:hover': { backgroundColor: 'rgba(92,255,157,0.12)', color: '#5cff9d' },
        }}
      >
        <span style={{ fontSize: 16, lineHeight: 1 }}>{glyph}</span>
      </IconButton>
    </Tooltip>
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
        borderRadius: '12px',
        border: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(8, 13, 12, 0.88)',
        backdropFilter: 'blur(16px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
      }}
    >
      <Btn label="Rotate left" glyph="⟲" onClick={() => cameraControl.orbit(-24, 0)} />
      <Btn label="Rotate right" glyph="⟳" onClick={() => cameraControl.orbit(24, 0)} />
      <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
      <Btn label="Tilt up" glyph="⤴" onClick={() => cameraControl.orbit(0, 12)} />
      <Btn label="Tilt down" glyph="⤵" onClick={() => cameraControl.orbit(0, -12)} />
      <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
      <Btn label="Zoom in" glyph="＋" onClick={() => cameraControl.zoom(0.55)} />
      <Btn label="Zoom out" glyph="－" onClick={() => cameraControl.zoom(1.8)} />
      <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
      <Btn label="Top-down (read rasters)" glyph="▭" onClick={() => cameraControl.topDown()} />
      <Btn label="Reset framing" glyph="⌂" onClick={() => cameraControl.reset()} />
    </Box>
  )
}
