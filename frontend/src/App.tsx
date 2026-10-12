/**
 * App shell (spec §1.1 `App.tsx`, item C9).
 *
 * Full-bleed Cesium viewer with the HUD chrome floating on top of it: a brand
 * block top-left, the plain-language prompt pill bottom-centre, and a rotated
 * coordinate strip along the right edge — the reference frame's "the map is the
 * screen, the UI is instruments on it" posture. The drawer holds the reporting
 * panels.
 *
 * The panel width is published to the view layer rather than kept here, because
 * the camera has to know how much of the canvas the drawer covers in order to
 * frame the study area in the middle of what is actually visible. See
 * `setPanelInsetLeft`.
 */
import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Divider from '@mui/material/Divider'
import Drawer from '@mui/material/Drawer'
import Fab from '@mui/material/Fab'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import AppErrorBoundary from './components/AppErrorBoundary'
import CameraController from './components/CameraController'
import LayerToggle from './components/LayerToggle'
import PromptBar from './components/PromptBar'
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
  const scene = useSceneStore((s) => s.scene)
  const env = scene?.envParams

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

  const coords = scene
    ? `${scene.center.coordinates[1].toFixed(4)}N ${scene.center.coordinates[0].toFixed(4)}E`
    : '—'

  return (
    <AppErrorBoundary>
      <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
        <SandboxViewer />

        {/* Always mounted, so the toggle can sit in the panel header. */}
        <Drawer
          variant="permanent"
          sx={{ display: panelOpen ? 'block' : 'none' }}
          PaperProps={{
            sx: {
              width: PANEL_WIDTH,
              // The theme's drawer paper is the translucent blurred pane; the
              // width is all that is scene-specific here.
            },
          }}
        >
          <Box sx={{ overflowY: 'auto' }}>
            <ScenePanel />
            <Divider />
            <ViewModeSwitch />
            <Divider />
            <LayerToggle />
            <CameraController />
            <Box sx={{ p: 2, pt: 0 }}>
              <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary' }}>
                PROTOTYPE — NOT ENGINEERING-GRADE. BASE IMAGERY (C) OPENSTREETMAP CONTRIBUTORS.
              </Typography>
            </Box>
          </Box>
        </Drawer>

        {/* ---- HUD chrome, floating over the canvas ---- */}

        {/* Panel toggle + brand block, top-left. */}
        <Box sx={{ position: 'absolute', top: 12, left: 12, zIndex: 1300 }}>
          <Stack direction="row" spacing={1.25} alignItems="center">
            <Fab
              size="small"
              aria-label={panelOpen ? 'Hide the control panel' : 'Show the control panel'}
              aria-expanded={panelOpen}
              onClick={() => setPanelOpen((open) => !open)}
            >
              {panelOpen ? '\u2715' : '\u2630'}
            </Fab>
            <Stack
              direction="row"
              spacing={1}
              alignItems="center"
              sx={{
                px: 1.25,
                py: 0.75,
                border: '1px solid',
                borderColor: 'divider',
                backgroundColor: 'rgba(8, 13, 12, 0.86)',
                backdropFilter: 'blur(12px)',
                borderRadius: '8px',
              }}
            >
              <Box component="span" className="hud-dot" sx={{ color: '#5cff9d' }} />
              <Box>
                <Typography className="hud-label" sx={{ fontSize: 9, lineHeight: 1.3 }}>
                  {scene?.name ?? 'HSK / HT SANDBOX'}
                </Typography>
                <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.3 }}>
                  {coords} · SPONGE- CITY SCREENING
                </Typography>
              </Box>
            </Stack>
          </Stack>
        </Box>

        {/* Live-environment readout, bottom-right: the two run inputs, always
            visible, mirroring the sliders in the drawer. Static chrome — it
            never intercepts camera gestures. */}
        <Box
          sx={{
            position: 'absolute',
            right: 14,
            bottom: 16,
            zIndex: 1100,
            pointerEvents: 'none',
            textAlign: 'right',
          }}
        >
          <Typography className="hud-mono" sx={{ fontSize: 10, color: 'text.secondary', lineHeight: 1.6 }}>
            RAINFALL{' '}
            <Box component="span" sx={{ color: '#5cff9d' }}>
              {Number(env?.precipitation ?? 450).toFixed(0)} MM
            </Box>
            {'  ·  '}TEMP{' '}
            <Box component="span" sx={{ color: '#ff9d2e' }}>
              {Number(env?.temperature ?? 30).toFixed(0)}°C
            </Box>
          </Typography>
          <Typography className="hud-mono" sx={{ fontSize: 9, color: 'rgba(125,146,138,0.6)', lineHeight: 1.6 }}>
            SCREENING MODEL — NOT A DRAINAGE SIMULATION
          </Typography>
        </Box>

        {/* Rotated coordinate strip along the right edge, like the reference
            frame's vertical annotation. */}
        <Box className="hud-edge-text" sx={{ position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)', zIndex: 1100 }}>
          HUNG SHUI KIU / HA TSUEN — NORTHERN METROPOLIS — {coords}
        </Box>

        {/* The one question box, bottom-centre. */}
        <PromptBar />
      </Box>
    </AppErrorBoundary>
  )
}
