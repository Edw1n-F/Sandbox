/**
 * The states between "app opened" and "a sandbox you can use".
 *
 * Two states were uncovered before this existed: **a scene with no
 * terrain**, and a plan with no interventions. The second is reported by
 * `StatsPanel` (it needs a run result to say anything), so this component owns
 * the first.
 *
 * Why it matters: the viewer cannot initialise without both a scene and its
 * terrain metadata, and before this existed the failure mode was a blank canvas
 * with working chrome around it — which reads as "the map is broken" rather than
 * "an artefact is missing". Naming the missing artefact is the difference
 * between a bug report and a fix.
 *
 * Deliberately plain text plus the remedy, never a stack trace: the audience is
 * a planner, and the actionable part is which script to re-run.
 */
import Alert from '@mui/material/Alert'
import AlertTitle from '@mui/material/AlertTitle'
import Box from '@mui/material/Box'

import { useSceneStore } from '@/state/sceneStore'

export default function SceneNotice() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const scene = useSceneStore((s) => s.scene)
  const terrain = useSceneStore((s) => s.terrain)
  const loading = useSceneStore((s) => s.loading)
  const error = useSceneStore((s) => s.error)

  let notice: { title: string; body: string; severity: 'error' | 'warning' | 'info' } | null = null

  if (error) {
    notice = {
      severity: 'error',
      title: 'Could not load the scene',
      body: `${error} — check the backend is running and the scene id is right.`,
    }
  } else if (loading) {
    notice = {
      severity: 'info',
      title: 'Loading the sandbox…',
      body: 'Fetching the scene and its terrain.',
    }
  } else if (!sceneId) {
    notice = {
      severity: 'warning',
      title: 'No scene configured',
      body: 'VITE_SCENE_ID is empty, so there is nothing to anchor the sandbox to.',
    }
  } else if (scene && !terrain) {
    // A scene that loaded but has no terrain for the viewer to stand on.
    notice = {
      severity: 'warning',
      title: 'This scene has no terrain',
      body:
        'The scene loaded but carries no terrain, so the 3D sandbox cannot draw. '
        + 'Re-run scripts/fetch_dem.py, then scripts/seed_scene.py --reset.',
    }
  }

  if (!notice) return null

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 12,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1400,
        width: 'min(460px, calc(100% - 24px))',
        pointerEvents: 'none',
      }}
    >
      <Alert severity={notice.severity} variant="outlined" sx={{ backgroundColor: 'rgba(8, 13, 12, 0.92)', backdropFilter: 'blur(12px)' }}>
        <AlertTitle sx={{ fontSize: 13, mb: 0.25 }}>{notice.title}</AlertTitle>
        <Box sx={{ fontSize: 11.5, lineHeight: 1.5 }}>{notice.body}</Box>
      </Alert>
    </Box>
  )
}
