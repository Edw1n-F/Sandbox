/**
 * A clean rounded section card.
 *
 * Replaces `Paper variant="outlined"` across the panel. Constellation-style:
 * near-black translucent body, hairline border, rounded corners, an optional
 * uppercase mono title row with a right-aligned slot. The earlier corner-bracket
 * militaristic flourish was dropped per the restyle brief — legible and calm
 * over decorated.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import type { ReactNode } from 'react'
import type { SxProps, Theme } from '@mui/material/styles'

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
        borderRadius: 10,
        backgroundColor: 'rgba(8, 13, 12, 0.72)',
        backdropFilter: 'blur(10px)',
        p: 1.75,
        ...sx,
      }}
    >
      {(title || right) && (
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mb: title ? 1.25 : 0 }}
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
