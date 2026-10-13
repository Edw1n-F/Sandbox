/**
 * Full-screen loading overlay while the Open3Dhk tilesets initialise.
 *
 * Lifts when every tileset the scene advertises has either finished its first view
 * (`ready`) or failed, so a missing tileset cannot hold the app hostage. A hard timeout
 * lifts it regardless: the tiles keep streaming in behind it, which is better than a
 * screen that never goes away.
 */
import { useEffect, useState } from 'react'
import Box from '@mui/material/Box'
import CircularProgress from '@mui/material/CircularProgress'
import Fade from '@mui/material/Fade'
import Typography from '@mui/material/Typography'

import { useSceneStore } from '@/state/sceneStore'
import { useTileLoadStore, type TilesetName } from '@/state/tileLoadStore'

const MAX_WAIT_MS = 60_000

export default function LoadingScreen() {
  const sceneLoading = useSceneStore((s) => s.loading)
  const sceneError = useSceneStore((s) => s.error)
  const terrain = useSceneStore((s) => s.terrain)
  const status = useTileLoadStore((s) => s.status)
  const pending = useTileLoadStore((s) => s.pending)
  const [timedOut, setTimedOut] = useState(false)

  useEffect(() => {
    const id = window.setTimeout(() => setTimedOut(true), MAX_WAIT_MS)
    return () => window.clearTimeout(id)
  }, [])

  const expected: TilesetName[] = []
  if (terrain?.terrain3DUrl) expected.push('terrain')
  if (terrain?.cityModel3DUrl ?? terrain?.cityModel3dUrl) expected.push('buildings')

  const settled = (n: TilesetName) => status[n] === 'ready' || status[n] === 'failed'
  const waitingOn = expected.filter((n) => !settled(n))
  const done = !sceneLoading && terrain != null && waitingOn.length === 0

  const visible = !sceneError && !timedOut && !done
  const inFlight = (pending.terrain ?? 0) + (pending.buildings ?? 0)

  let message = 'Loading scene'
  if (!sceneLoading && terrain) {
    message = 'Initialising Open3Dhk terrain and buildings'
  }

  return (
    <Fade in={visible} timeout={{ enter: 0, exit: 500 }} unmountOnExit>
      <Box
        role="status"
        aria-live="polite"
        sx={{
          position: 'absolute',
          inset: 0,
          zIndex: 2000,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 2,
          bgcolor: 'background.default',
        }}
      >
        <CircularProgress size={40} thickness={3} />
        <Typography variant="subtitle1">{message}</Typography>
        <Typography variant="caption" color="text.secondary">
          {inFlight > 0
            ? `${inFlight} tile${inFlight === 1 ? '' : 's'} loading`
            : 'The first load streams about 300 MB of building tiles'}
        </Typography>
      </Box>
    </Fade>
  )
}
