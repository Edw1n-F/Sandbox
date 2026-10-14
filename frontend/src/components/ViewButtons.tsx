/**
 * Bottom-centre view-control pill.
 *
 * Carries every camera move reachable without a particular input device: tilt and
 * orbit and zoom as buttons, a jump to top-down, a re-frame, and a help button.
 * Tilt used to be gesture-only (right-drag or a two-finger trackpad drag), which
 * left a touch user or a mouse without a right button no way to look up or down.
 * The gestures still work; the buttons are the floor, not a replacement.
 *
 * The compass sits on the left of the pill. It points at north and rotates as the
 * view does, so a user who has spun the scene can see that they have and what the
 * reset button is for. Pressing it faces north, which is the gesture people try on
 * every map.
 */
import Box from '@mui/material/Box'
import ClickAwayListener from '@mui/material/ClickAwayListener'
import IconButton from '@mui/material/IconButton'
import Typography from '@mui/material/Typography'
import { useEffect, useState } from 'react'

import { cameraControl } from '@/view/SandboxViewer'
import { isNorthUp, offNorth, useCameraHeading } from '@/view/cameraHeading'
import { hasSeenNavHint, markNavHintSeen, NAV_HELP } from './navHelp'

const TILT_STEP_DEG = 8
const ORBIT_STEP_DEG = 24

/** One rounded icon button. Unicode glyphs keep the bundle free of icon deps. */
function Btn({
  label,
  glyph,
  onClick,
  active = false,
}: {
  label: string
  glyph: string
  onClick: () => void
  active?: boolean
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
        color: active ? '#5cff9d' : '#dce8e2',
        backgroundColor: active ? 'rgba(92,255,157,0.12)' : undefined,
        '&:hover': { backgroundColor: 'rgba(92,255,157,0.12)', color: '#5cff9d' },
      }}
    >
      <span style={{ fontSize: 16, lineHeight: 1 }} aria-hidden="true">
        {glyph}
      </span>
    </IconButton>
  )
}

/** A compass needle that turns against the view, so north stays north. */
function Compass() {
  const heading = useCameraHeading()
  const northUp = isNorthUp(heading)
  return (
    <IconButton
      aria-label={northUp ? 'Facing north' : `Facing ${Math.round(heading)} degrees — face north`}
      title={northUp ? 'Facing north' : 'Face north'}
      onClick={() => {
        // Rotate by the shortest way round to north rather than snapping.
        cameraControl.orbit(-offNorth(heading), 0)
      }}
      size="small"
      sx={{ width: 32, height: 32, borderRadius: '6px', '&:hover': { backgroundColor: 'rgba(92,255,157,0.12)' } }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 22,
          height: 22,
          borderRadius: '50%',
          border: '1px solid',
          borderColor: northUp ? 'divider' : '#5cff9d',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          transition: 'border-color 0.2s',
        }}
      >
        {/* The needle turns the opposite way to the camera, so it keeps pointing
            at north however the view has been rotated. */}
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            transform: `rotate(${-heading}deg)`,
            transition: 'transform 0.08s linear',
          }}
        >
          <Box sx={{ position: 'absolute', left: '50%', top: 2, width: 0, height: 0, ml: '-3px',
            borderLeft: '3px solid transparent', borderRight: '3px solid transparent',
            borderBottom: '8px solid #ff6b6b' }} />
          <Box sx={{ position: 'absolute', left: '50%', bottom: 2, width: 0, height: 0, ml: '-3px',
            borderLeft: '3px solid transparent', borderRight: '3px solid transparent',
            borderTop: '8px solid rgba(220,232,226,0.45)' }} />
        </Box>
      </Box>
    </IconButton>
  )
}

/** The gesture cheat-sheet. Non-modal: it must not trap the user over the map. */
function NavHelp({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <Box
      role="dialog"
      aria-label="How to move around the map"
      sx={{
        position: 'absolute',
        bottom: 'calc(100% + 10px)',
        left: '50%',
        transform: 'translateX(-50%)',
        width: 'min(420px, 90vw)',
        p: 1.5,
        borderRadius: '10px',
        border: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(8, 13, 12, 0.96)',
        backdropFilter: 'blur(16px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
      }}
    >
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.75 }}>
        <Typography className="hud-label">Moving around</Typography>
        <IconButton aria-label="Close help" size="small" onClick={onClose} sx={{ width: 24, height: 24, color: 'text.secondary' }}>
          <span aria-hidden="true" style={{ fontSize: 12 }}>✕</span>
        </IconButton>
      </Box>
      {NAV_HELP.map((group) => (
        <Box key={group.title} sx={{ mb: 0.75 }}>
          <Typography className="hud-label" sx={{ fontSize: 8.5, mb: 0.25 }}>{group.title}</Typography>
          {group.hints.map((h) => (
            <Box key={h.action} sx={{ display: 'flex', gap: 1, py: 0.15 }}>
              <Typography className="hud-mono" sx={{ fontSize: 11, minWidth: 108, color: '#5cff9d' }}>
                {h.action}
              </Typography>
              <Typography sx={{ fontSize: 11.5, color: 'text.secondary' }}>{h.effect}</Typography>
            </Box>
          ))}
        </Box>
      ))}
    </Box>
  )
}

export default function ViewButtons() {
  // Open on the first visit only, so a new user is told once and a returning one
  // is never nagged. It is reachable again from the ? button at any time.
  const [helpOpen, setHelpOpen] = useState<boolean>(() => !hasSeenNavHint())

  const close = () => {
    setHelpOpen(false)
    markNavHintSeen()
  }

  return (
    <ClickAwayListener onClickAway={() => helpOpen && close()}>
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
        {helpOpen && <NavHelp onClose={close} />}

        <Compass />
        <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
        <Btn label="Orbit left" glyph="⟲" onClick={() => cameraControl.orbit(-ORBIT_STEP_DEG, 0)} />
        <Btn label="Orbit right" glyph="⟳" onClick={() => cameraControl.orbit(ORBIT_STEP_DEG, 0)} />
        <Btn label="Tilt up (towards the horizon)" glyph="⤒" onClick={() => cameraControl.orbit(0, TILT_STEP_DEG)} />
        <Btn label="Tilt down (towards overhead)" glyph="⤓" onClick={() => cameraControl.orbit(0, -TILT_STEP_DEG)} />
        <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
        <Btn label="Zoom in" glyph="＋" onClick={() => cameraControl.zoom(0.55)} />
        <Btn label="Zoom out" glyph="－" onClick={() => cameraControl.zoom(1.8)} />
        <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
        <Btn label="Top-down (read rasters)" glyph="▭" onClick={() => cameraControl.topDown()} />
        <Btn label="Reset framing" glyph="⌂" onClick={() => cameraControl.reset()} />
        <Box sx={{ width: 1, height: 20, bgcolor: 'divider', mx: 0.5 }} />
        <Btn
          label="How to move around"
          glyph="?"
          active={helpOpen}
          onClick={() => (helpOpen ? close() : setHelpOpen(true))}
        />
      </Box>
    </ClickAwayListener>
  )
}
