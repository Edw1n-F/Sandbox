/**
 * App shell — reorganized layout (spec §1.1 `App.tsx`, item C9).
 *
 * The 3D sandbox is full-bleed and the chrome floats on it:
 *   - left, full height: the LLM chat panel
 *   - top-right: scenario sliders + Run, and below it the project catalog
 *   - bottom-right: the compact stats panel
 *   - bottom-centre: view-control buttons (rotate / zoom / reset)
 *   - top-left: small brand block
 *
 * The camera has to know how much canvas the chat panel covers on the left, so
 * the study area is framed in the middle of what is actually visible. See
 * `setPanelInsetLeft`.
 */
import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import Fab from '@mui/material/Fab'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import AppErrorBoundary from './components/AppErrorBoundary'
import ChatPanel from './components/ChatPanel'
import ProjectCatalog from './components/ProjectCatalog'
import SlidersPanel from './components/SlidersPanel'
import StatsPanel from './components/StatsPanel'
import ViewButtons from './components/ViewButtons'
import { usePlanStore } from './state/planStore'
import { useSceneStore } from './state/sceneStore'
import SandboxViewer, { setPanelInsetLeft } from './view/SandboxViewer'

const CHAT_WIDTH = 340

export default function App() {
  const sceneId = import.meta.env.VITE_SCENE_ID ?? ''
  const loadScene = useSceneStore((s) => s.load)
  const loadPlans = usePlanStore((s) => s.load)
  const scene = useSceneStore((s) => s.scene)

  const [chatOpen, setChatOpen] = useState(true)

  useEffect(() => {
    if (!sceneId) return
    void loadScene(sceneId)
    void loadPlans(sceneId)
  }, [sceneId, loadScene, loadPlans])

  // The camera framing has to know how much canvas the chat panel covers, or it
  // centres the study area in the full width and hides a third of it behind the panel.
  useEffect(() => {
    setPanelInsetLeft(chatOpen ? CHAT_WIDTH : 0)
  }, [chatOpen])

  const coords = scene
    ? `${scene.center.coordinates[1].toFixed(4)}N ${scene.center.coordinates[0].toFixed(4)}E`
    : '—'

  return (
    <AppErrorBoundary>
      <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
        <SandboxViewer />

        {chatOpen && <ChatPanel />}

        {/* Top-right column: scenario inputs + project catalog */}
        <Box sx={{ position: 'absolute', top: 12, right: 12, zIndex: 1200, display: 'flex', flexDirection: 'column', gap: 1.25, width: 300 }}>
          <SlidersPanel />
          <ProjectCatalog />
        </Box>

        {/* Bottom-right: stats */}
        <Box sx={{ position: 'absolute', right: 12, bottom: 16, zIndex: 1200 }}>
          <StatsPanel />
        </Box>

        {/* Bottom-centre: view buttons */}
        <ViewButtons />

        {/* Top-left: brand block + chat toggle */}
        <Box sx={{ position: 'absolute', top: 12, left: chatOpen ? CHAT_WIDTH + 12 : 12, zIndex: 1300, transition: 'left 0.2s' }}>
          <Stack direction="row" spacing={1.25} alignItems="center">
            <Fab
              size="small"
              aria-label={chatOpen ? 'Hide the chat panel' : 'Show the chat panel'}
              aria-expanded={chatOpen}
              onClick={() => setChatOpen((o) => !o)}
            >
              {chatOpen ? '\u2715' : '\u2630'}
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
                borderRadius: '10px',
              }}
            >
              <Box component="span" className="hud-dot" sx={{ color: '#5cff9d' }} />
              <Box>
                <Typography className="hud-label" sx={{ fontSize: 9, lineHeight: 1.3 }}>
                  {scene?.name ?? 'HSK / HT SANDBOX'}
                </Typography>
                <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary', lineHeight: 1.3 }}>
                  {coords}
                </Typography>
              </Box>
            </Stack>
          </Stack>
        </Box>
      </Box>
    </AppErrorBoundary>
  )
}
