/**
 * The details card: what the planner decides about an intervention before it is
 * placed.
 *
 * One row per value the run actually reads. Each row asks a plain question and
 * offers two answers: **Let the AI estimate** (the default, with the number and
 * where it comes from shown underneath) or **Enter my own**, which opens a
 * field. The caller holds the Add/Save button back until every row is answered
 * — `allAnswered` in `inputAnswers.ts` is the one rule for that.
 *
 * Only values the ledger reads become questions. Location, footprint and the
 * surface replaced are facts of the served site, not choices the run consumes,
 * so they are shown as a summary line elsewhere rather than asked here; a
 * question whose answer changes nothing is worse than no question.
 *
 * `variant="settings"` is the same card laid out as a settings panel (label on
 * the left, control on the right) for editing an intervention already placed.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import ToggleButton from '@mui/material/ToggleButton'
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup'
import Typography from '@mui/material/Typography'

import type { SandboxInput } from '@/api/sandboxApi'
import { fieldError, type InputAnswer, type InputAnswers } from './inputAnswers'

export default function InputCard({
  inputs,
  answers,
  onChange,
  disabled,
  variant = 'card',
}: {
  inputs: SandboxInput[]
  answers: InputAnswers
  onChange: (next: InputAnswers) => void
  disabled?: boolean
  variant?: 'card' | 'settings'
}) {
  if (inputs.length === 0) {
    return (
      <Typography sx={{ fontSize: 10, color: 'text.secondary', lineHeight: 1.4 }}>
        Nothing to decide: the run applies this intervention's land-cover change as it stands.
      </Typography>
    )
  }
  const settings = variant === 'settings'
  return (
    <Stack spacing={settings ? 0 : 1}>
      {inputs.map((input, index) => {
        const answer = answers[input.key] ?? { mode: 'ai' as const, text: String(input.estimate) }
        const error = fieldError(input, answer)
        const set = (patch: Partial<InputAnswer>) =>
          onChange({ ...answers, [input.key]: { ...answer, ...patch } })

        const choice = (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={answer.mode}
            disabled={disabled}
            onChange={(_, next) => next && set({ mode: next })}
            aria-label={`${input.label}: who sets it`}
            sx={{ '& .MuiToggleButton-root': { px: 1, py: 0.25, fontSize: 10, textTransform: 'none' } }}
          >
            <ToggleButton value="ai">Let the AI estimate</ToggleButton>
            <ToggleButton value="user">Enter my own</ToggleButton>
          </ToggleButtonGroup>
        )

        const value =
          answer.mode === 'ai' ? (
            <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
              AI estimate: {input.estimate} {input.unit} · {input.estimateBasis}
            </Typography>
          ) : (
            <TextField
              size="small"
              type="number"
              value={answer.text}
              disabled={disabled}
              autoFocus
              onChange={(e) => set({ text: e.target.value })}
              error={error !== null}
              helperText={error ?? `${input.min}–${input.max} ${input.unit}`}
              slotProps={{
                htmlInput: { min: input.min, max: input.max, step: 0.1, 'aria-label': input.label },
                input: { endAdornment: <Typography sx={{ fontSize: 11, color: 'text.secondary', ml: 0.5 }}>{input.unit}</Typography> },
              }}
              sx={{ width: 150, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }}
            />
          )

        if (settings) {
          return (
            <Box
              key={input.key}
              sx={{
                py: 1,
                borderTop: index === 0 ? 'none' : '1px solid',
                borderColor: 'divider',
              }}
            >
              <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                <Typography sx={{ fontSize: 11, fontWeight: 600 }}>{input.label}</Typography>
                {choice}
              </Stack>
              <Box sx={{ mt: 0.5 }}>{value}</Box>
            </Box>
          )
        }

        return (
          <Box key={input.key}>
            <Typography sx={{ fontSize: 10.5, lineHeight: 1.3, mb: 0.5 }}>
              {inputs.length > 1 ? `${index + 1}. ` : ''}
              {input.label}?
            </Typography>
            <Box sx={{ mb: 0.5 }}>{choice}</Box>
            {value}
          </Box>
        )
      })}
    </Stack>
  )
}
