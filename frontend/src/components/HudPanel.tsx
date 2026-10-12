/**
 * A HUD-framed section: hairline box, corner brackets, uppercase mono title.
 *
 * Replaces `Paper variant="outlined"` everywhere in the panel. The corner
 * brackets are the one flourish borrowed whole from the reference frame —
 * four 8 px L-shapes, accent-tinted, purely decorative.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import type { CSSProperties, ReactNode } from 'react'
import type { SxProps, Theme } from '@mui/material/styles'

/** One L-shaped corner. */
function Corner({ pos }: { pos: 'tl' | 'tr' | 'bl' | 'br' }) {
  const base: CSSProperties = {
    position: 'absolute',
    width: 8,
    height: 8,
    borderColor: 'rgba(92, 255, 157, 0.55)',
    borderStyle: 'solid',
    borderWidth: 0,
  }
  const where: Record<typeof pos, CSSProperties> = {
    tl: { top: -1, left: -1, borderTopWidth: 1, borderLeftWidth: 1 },
    tr: { top: -1, right: -1, borderTopWidth: 1, borderRightWidth: 1 },
    bl: { bottom: -1, left: -1, borderBottomWidth: 1, borderLeftWidth: 1 },
    br: { bottom: -1, right: -1, borderBottomWidth: 1, borderRightWidth: 1 },
  }
  return <Box aria-hidden sx={{ ...base, ...where[pos] }} />
}

interface Props {
  title?: string
  /** Right-aligned slot in the title row: a status chip, a "hide" toggle. */
  right?: ReactNode
  children: ReactNode
  sx?: SxProps<Theme>
}

export default function HudPanel({ title, right, children, sx }: Props) {
  return (
    <Box
      sx={{
        position: 'relative',
        border: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(5, 8, 10, 0.45)',
        p: 1.5,
        ...sx,
      }}
    >
      <Corner pos="tl" />
      <Corner pos="tr" />
      <Corner pos="bl" />
      <Corner pos="br" />

      {(title || right) && (
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: title ? 1 : 0 }}
        >
          {title ? (
            <Typography className="hud-label" component="div">
              {title}
            </Typography>
          ) : (
            <Box />
          )}
          {right}
        </Stack>
      )}
      {children}
    </Box>
  )
}
