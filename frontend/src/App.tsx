/**
 * App shell (spec §1.1 `App.tsx`, item C9).
 *
 * Full-bleed Cesium viewer with a MUI panel overlaid on the left. The scene is
 * the entry anchor (CLAUDE.md): `VITE_SCENE_ID` selects the scene, and
 * everything downstream is driven by the two stores.
 */
import { useEffect } from 'react'
import Box from '@mui/material/Box'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import Typography from '@mui/material/Typography'

import CameraController from './components/CameraController'
import LayerToggle from './components/LayerToggle'
import ScenePanel from './components/ScenePanel'
import { usePlanStore } from './state/planStore'
import { useSceneStore } from './state/sceneStore'
import SandboxViewer from './view/SandboxViewer'

const PANEL_WIDTH = 340

export default function App() {
  const sceneId = import.meta.env.VITE_SCENE_ID ?? ''
  const loadScene = useSceneStore((s) => s.load)
  const loadPlans = usePlanStore((s) => s.load)

  useEffect(() => {
    if (!sceneId) return
    void loadScene(sceneId)
    void loadPlans(sceneId)
  }, [sceneId, loadScene, loadPlans])

  return (
    <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
      <SandboxViewer />

      <Drawer
        variant="permanent"
        PaperProps={{ sx: { width: PANEL_WIDTH, bgcolor: 'background.paper' } }}
      >
        <Box sx={{ overflowY: 'auto' }}>
          <ScenePanel />
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
    </Box>
  )
}
