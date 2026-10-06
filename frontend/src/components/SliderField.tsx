/**
 * Labelled slider with a live value readout.
 *
 * Extracted rather than inlined so `ScenePanel` and any future scenario control
 * share one accessible implementation -- MUI's `aria-labelledby` wiring is easy
 * to get subtly wrong and impossible to notice by eye.
 */
import Box from '@mui/material/Box'
import Slider from '@mui/material/Slider'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

interface Props {
  label: string
  value: number
  min: number
  max: number
  unit: string
  step?: number
  onChange: (value: number) => void
}

export default function SliderField({
  label,
  value,
  min,
  max,
  unit,
  step = 1,
  onChange,
}: Props) {
  const id = `${label.toLowerCase()}-slider`
  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle2" id={id}>
          {label}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {value} {unit}
        </Typography>
      </Stack>
      <Slider
        aria-labelledby={id}
        size="small"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(_event, next) => onChange(Array.isArray(next) ? next[0] : next)}
      />
    </Box>
  )
}
