/**
 * The input card: the values the run will read for one intervention.
 *
 * Each value is either typed by the user or left to the AI's estimate, and the
 * card says which estimate stands in (its value and where it comes from). The
 * answer is sent as `values`; a key left out is the user handing that value to
 * the AI, which the backend records on the feature as `ai_estimate`.
 *
 * A kind with nothing the run reads shows no fields and says so, rather than
 * asking a question whose answer would change nothing.
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
}: {
  inputs: SandboxInput[]
  answers: InputAnswers
  onChange: (next: InputAnswers) => void
  disabled?: boolean
}) {
  if (inputs.length === 0) {
    return (
      <Typography sx={{ fontSize: 10, color: 'text.secondary', lineHeight: 1.4 }}>
        No values to set: the run applies this kind's land-cover change as it stands.
      </Typography>
    )
  }
  return (
    <Stack spacing={1}>
      {inputs.map((input) => {
        const answer = answers[input.key] ?? { mode: 'ai' as const, text: String(input.estimate) }
        const error = fieldError(input, answer)
        const set = (patch: Partial<InputAnswer>) =>
          onChange({ ...answers, [input.key]: { ...answer, ...patch } })
        return (
          <Box key={input.key}>
            <Typography sx={{ fontSize: 10.5, lineHeight: 1.3, mb: 0.5 }}>{input.label}</Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={answer.mode}
              disabled={disabled}
              onChange={(_, next) => next && set({ mode: next })}
              aria-label={`${input.label}: who sets it`}
              sx={{ mb: 0.5, '& .MuiToggleButton-root': { px: 1, py: 0.25, fontSize: 10, textTransform: 'none' } }}
            >
              <ToggleButton value="ai">Let the AI estimate</ToggleButton>
              <ToggleButton value="user">Enter my own</ToggleButton>
            </ToggleButtonGroup>
            {answer.mode === 'ai' ? (
              <Typography className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.4 }}>
                estimate {input.estimate} {input.unit} · {input.estimateBasis}
              </Typography>
            ) : (
              <TextField
                size="small"
                type="number"
                value={answer.text}
                disabled={disabled}
                onChange={(e) => set({ text: e.target.value })}
                error={error !== null}
                helperText={error ?? `${input.min}–${input.max} ${input.unit}`}
                slotProps={{
                  htmlInput: { min: input.min, max: input.max, step: 0.1, 'aria-label': input.label },
                }}
                sx={{ width: 140, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }}
              />
            )}
          </Box>
        )
      })}
    </Stack>
  )
}
