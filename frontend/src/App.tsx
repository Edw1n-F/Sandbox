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
import Button from '@mui/material/Button'
import Fab from '@mui/material/Fab'
import Stack from '@mui/material/Stack'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Typography from '@mui/material/Typography'

import AppErrorBoundary from './components/AppErrorBoundary'
import ChatPanel from './components/ChatPanel'
import InterventionLegend from './components/InterventionLegend'
import LoadingScreen from './components/LoadingScreen'
import ProjectCatalog from './components/ProjectCatalog'
import SceneNotice from './components/SceneNotice'
import SlidersPanel from './components/SlidersPanel'
import StatsPanel from './components/StatsPanel'
import InterventionsPanel from './components/InterventionsPanel'
import ViewButtons from './components/ViewButtons'
import { usePlanStore } from './state/planStore'
import { useSceneStore } from './state/sceneStore'
import SandboxViewer, { setPanelInsetLeft } from './view/SandboxViewer'

const CHAT_WIDTH = 340

function LayerToggles() {
  const layers = useSceneStore((s) => s.layers)
  const toggleLayer = useSceneStore((s) => s.toggleLayer)
  const setLayers = useSceneStore((s) => s.setLayers)
  const terrain = useSceneStore((s) => s.terrain)
  const hasWet = Boolean(terrain?.wetGridUrl)
  const hasHeat = Boolean(terrain?.heatmapUrl)
  return (
    <Stack spacing={0.75} sx={{ alignItems: 'stretch' }}>
      <Button
        size="small"
        variant={layers.wetGrid ? 'contained' : 'outlined'}
        disabled={!hasWet}
        onClick={() => toggleLayer('wetGrid')}
        sx={{ minWidth: 0, px: 1, py: 0.4, fontSize: 10, lineHeight: 1 }}
      >
        Flood
      </Button>
      <Button
        size="small"
        variant={layers.heatmap ? 'contained' : 'outlined'}
        disabled={!hasHeat}
        onClick={() => toggleLayer('heatmap')}
        sx={{ minWidth: 0, px: 1, py: 0.4, fontSize: 10, lineHeight: 1 }}
      >
        Heat
      </Button>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={layers.massing25d ? '2.5d' : '3d'}
        onChange={(_, next) => {
          if (next) setLayers({ massing25d: next === '2.5d' })
        }}
        aria-label="Building style"
        sx={{
          '& .MuiToggleButton-root': {
            px: 1, py: 0.4, fontSize: 10, lineHeight: 1, minWidth: 0,
            textTransform: 'none',
          },
        }}
      >
        <ToggleButton value="3d" aria-label="Textured 3D buildings">3D</ToggleButton>
        <ToggleButton value="2.5d" aria-label="2.5D massing">2.5D</ToggleButton>
      </ToggleButtonGroup>
    </Stack>
  )
}

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

        {/* Names the missing artefact when the sandbox cannot draw:
            no terrain, no scene id, or a failed load. Without it the failure
            mode is a blank canvas with working chrome, which reads as broken. */}
        <SceneNotice />

        <LoadingScreen />

        {chatOpen && <ChatPanel />}

        {/* Right column: scenario inputs, project catalog, stats. The whole
            column scrolls as ONE unit (not the catalog alone) so the catalog's
            project toggles are never clipped by a sibling panel or a fixed
            bottom anchor. pointer-events:auto on the scroll surface lets the
            column scroll; HudPanel opts each panel back in, and the thin
            scrollbar + panel padding keep canvas drags reachable outside it. */}
        <Box sx={{
          position: 'absolute', top: 12, right: 12, bottom: 16, zIndex: 1200,
          display: 'flex', flexDirection: 'column', gap: 1.25, width: 300,
          pointerEvents: 'none',
        }}>
          {/* Wet/Heat toggles sit to the LEFT of the scenario inputs, aligned
              to the top of that panel, not stacked on top of it. */}
          <Box sx={{
            position: 'absolute', top: 0, right: '100%', mr: 0.75,
            display: 'flex', alignItems: 'flex-start', pointerEvents: 'auto',
          }}>
            <LayerToggles />
          </Box>
          <Box sx={{
            display: 'flex', flexDirection: 'column', gap: 1.25,
            overflowY: 'auto', minHeight: 0, pointerEvents: 'auto',
            pr: 0.5, mr: -0.5,
          }}>
            <SlidersPanel />
            <ProjectCatalog />
            {/* What the user has placed on the sandbox. The run scores it with
                the toggled projects; deleting a row unpins it. */}
            <InterventionsPanel />
            <StatsPanel />
          </Box>
        </Box>

        {/* Bottom-centre: view buttons */}
        <ViewButtons />

        {/* Bottom-left: intervention legend, hidden until a plan has features. */}
        <InterventionLegend />

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
