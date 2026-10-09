/**
 * Camera controls (spec §1.1 `components/CameraController.tsx`, item C8).
 *
 * The viewer publishes these actions through `cameraControl` rather than taking
 * props, so the Cesium instance stays owned by `SandboxViewer` and this stays a
 * pure UI component (CLAUDE.md: components do not render).
 *
 * Three framings rather than two, because "fit the bbox" used to mean "look
 * straight down at it" — Cesium can only fit a `Rectangle` from overhead, because
 * a rectangle carries no orientation. That is a useful view for reading the
 * wet-grid and heatmap rasters and a useless one for a city, so both are offered by
 * name and the 3D one is the default. See `cameraRig.ts`.
 *
 * ## Why there are orbit buttons as well as gestures
 *
 * Rotation was available in Cesium all along, but behind middle-drag and Ctrl+drag,
 * while right-drag — the gesture people reach for to turn a model — zoomed. Buttons
 * and arrow keys are here because a mouse-only route to a core navigation action is a
 * route some people cannot take: right-drag is a two-finger click on a Mac, and
 * middle-drag does not exist on a trackpad at all.
 *
 * ## The hint text is measured, not assumed
 *
 * An earlier version of this component said "right-drag to orbit and tilt", which was
 * false — right-drag zoomed. The bindings below are the ones `cameraRig.ts` installs
 * and that `scripts/verify-3d.mjs` asserts on, so the caption cannot drift away from
 * the behaviour again without a check failing.
 */
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import ButtonGroup from '@mui/material/ButtonGroup'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import { useSceneStore } from '@/state/sceneStore'
import { cameraControl } from '@/view/SandboxViewer'

/**
 * Steps per press. Modest, so a repeated press does not overshoot into a spin.
 *
 * `ZOOM_FACTOR` is a distance multiplier: 0.8 moved the camera a fifth of the way per
 * press, which reads as nothing happening on a 3 km district. 0.55 is about a third.
 */
const ORBIT_STEP_DEG = 12
const TILT_STEP_DEG = 8
const ZOOM_FACTOR = 0.55

export default function CameraController() {
  const hasScene = useSceneStore((s) => Boolean(s.scene))

  return (
    <Box sx={{ p: 2, pt: 0 }}>
      <ButtonGroup size="small" fullWidth>
        <Button
          variant="outlined"
          disabled={!hasScene}
          onClick={() => cameraControl.flyToBbox()}
        >
          3D view
        </Button>
        <Button
          variant="outlined"
          disabled={!hasScene}
          onClick={() => cameraControl.topDown()}
        >
          Top-down
        </Button>
        <Button
          variant="outlined"
          disabled={!hasScene}
          onClick={() => cameraControl.reset()}
        >
          Reset
        </Button>
      </ButtonGroup>

      <ButtonGroup size="small" fullWidth sx={{ mt: 0.75 }}>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Rotate left"
          title="Rotate left"
          onClick={() => cameraControl.orbit(-ORBIT_STEP_DEG, 0)}
        >
          ↺
        </Button>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Tilt down"
          title="Tilt down (raise the horizon)"
          onClick={() => cameraControl.orbit(0, -TILT_STEP_DEG)}
        >
          ▼
        </Button>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Zoom in"
          title="Zoom in"
          onClick={() => cameraControl.zoom(ZOOM_FACTOR)}
        >
          +
        </Button>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Zoom out"
          title="Zoom out"
          onClick={() => cameraControl.zoom(1 / ZOOM_FACTOR)}
        >
          −
        </Button>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Tilt up"
          title="Tilt up"
          onClick={() => cameraControl.orbit(0, TILT_STEP_DEG)}
        >
          ▲
        </Button>
        <Button
          variant="text"
          disabled={!hasScene}
          aria-label="Rotate right"
          title="Rotate right"
          onClick={() => cameraControl.orbit(ORBIT_STEP_DEG, 0)}
        >
          ↻
        </Button>
      </ButtonGroup>

      <Stack spacing={0.25} sx={{ mt: 0.75 }}>
        <Typography variant="caption" color="text.secondary" display="block">
          Drag to pan · right-drag to orbit · arrow keys orbit and tilt
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          Pinch or ⌘-scroll to zoom · middle-drag or Ctrl+drag tilts
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          Two-finger scroll is off: it cannot pan the map reliably here, and zooming on
          it was surprising. A mouse wheel is the same browser event, so it is off too.
        </Typography>
      </Stack>
    </Box>
  )
}
