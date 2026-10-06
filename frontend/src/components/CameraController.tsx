/**
 * Camera controls (spec §1.1 `components/CameraController.tsx`, item C8).
 *
 * The viewer publishes these actions through `cameraControl` rather than taking
 * props, so the Cesium instance stays owned by `SandboxViewer` and this stays a
 * pure UI component (CLAUDE.md: components do not render).
 */
import Button from '@mui/material/Button'
import ButtonGroup from '@mui/material/ButtonGroup'

import { useSceneStore } from '@/state/sceneStore'
import { cameraControl } from '@/view/SandboxViewer'

export default function CameraController() {
  const hasScene = useSceneStore((s) => Boolean(s.scene))

  return (
    <ButtonGroup size="small" sx={{ p: 2, pt: 0 }} fullWidth>
      <Button
        variant="outlined"
        disabled={!hasScene}
        onClick={() => cameraControl.flyToBbox()}
      >
        Fit bbox
      </Button>
      <Button
        variant="outlined"
        disabled={!hasScene}
        onClick={() => cameraControl.reset()}
      >
        Reset view
      </Button>
    </ButtonGroup>
  )
}
