/**
 * App shell (spec §1.1 `App.tsx`, item C9).
 *
 * Full-bleed Cesium viewer with a MUI panel overlaid on the left. The scene is
 * the entry anchor (CLAUDE.md): `VITE_SCENE_ID` selects the scene, and
 * everything downstream is driven by the two stores.
 *
 * The panel width is published to the view layer rather than kept here, because
 * the camera has to know how much of the canvas the drawer covers in order to
 * frame the study area in the middle of what is actually visible. See
 * `setPanelInsetLeft`.
 */
import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Fab from '@mui/material/Fab'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import Typography from '@mui/material/Typography'

import CameraController from './components/CameraController'
import LayerToggle from './components/LayerToggle'
import ScenePanel from './components/ScenePanel'
import ViewModeSwitch from './components/ViewModeSwitch'
import { usePlanStore } from './state/planStore'
import { useSceneStore } from './state/sceneStore'
import SandboxViewer, { setPanelInsetLeft } from './view/SandboxViewer'

const PANEL_WIDTH = 340

/**
 * Below this width the panel is collapsed on first paint.
 *
 * The drawer is a fixed 340 px, which is fine beside a desktop canvas and catastrophic
 * on a phone: measured at 390 px wide it covered 100% of a 390 px viewport, leaving no
 * map at all and no way to get one back. Collapsing by default is the only version of
 * this that is usable on a small screen, and the toggle keeps it recoverable on desktop.
 */
const NARROW_VIEWPORT_PX = 768

export default function App() {
  const sceneId = import.meta.env.VITE_SCENE_ID ?? ''
  const loadScene = useSceneStore((s) => s.load)
  const loadPlans = usePlanStore((s) => s.load)

  const [panelOpen, setPanelOpen] = useState(
    () => typeof window === 'undefined' || window.innerWidth >= NARROW_VIEWPORT_PX,
  )

  useEffect(() => {
    if (!sceneId) return
    void loadScene(sceneId)
    void loadPlans(sceneId)
  }, [sceneId, loadScene, loadPlans])

  // The camera framing has to know how much canvas the drawer is covering, or it
  // centres the study area in the full width and hides a third of it behind the panel.
  // When the panel is collapsed there is nothing to shift for.
  useEffect(() => {
    setPanelInsetLeft(panelOpen ? PANEL_WIDTH : 0)
  }, [panelOpen])

  return (
    <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
      <SandboxViewer />

      {/* Always mounted, so the toggle can sit in the panel header. */}
      <Drawer
        variant="permanent"
        sx={{ display: panelOpen ? 'block' : 'none' }}
        PaperProps={{ sx: { width: PANEL_WIDTH, bgcolor: 'background.paper' } }}
      >
        <Box sx={{ overflowY: 'auto' }}>
          <ScenePanel />
          <Divider />
          <ViewModeSwitch />
          <Divider />
          <LayerToggle />
          <CameraController />
          <Box sx={{ p: 2, pt: 0 }}>
            <Typography variant="caption" color="text.secondary">
              Prototype — not engineering-grade. Base imagery (c) OpenStreetMap
              contributors.
            </Typography>
          </Box>
        </Box>
      </Drawer>

      {/* Floating, so it works identically whether the panel is open or closed, and it
          stays clear of the camera controls in the bottom-right. */}
      <Fab
        size="small"
        color="primary"
        aria-label={panelOpen ? 'Hide the control panel' : 'Show the control panel'}
        aria-expanded={panelOpen}
        onClick={() => setPanelOpen((open) => !open)}
        sx={{ position: 'absolute', top: 12, left: 12, zIndex: 1300 }}
      >
        {panelOpen ? '\u2715' : '\u2630'}
      </Fab>
    </Box>
  )
}
