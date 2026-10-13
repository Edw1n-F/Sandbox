/**
 * Labelled slider with a live value readout.
 *
 * Extracted rather than inlined so every scenario control shares one accessible
 * implementation -- MUI's `aria-labelledby` wiring is easy
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
  marks?: Array<{ value: number; label?: string }>
  caption?: string
  onChange: (value: number) => void
}

export default function SliderField({
  label,
  value,
  min,
  max,
  unit,
  step = 1,
  marks,
  caption,
  onChange,
}: Props) {
  const id = `${label.toLowerCase()}-slider`
  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="baseline" sx={{ mb: 0.25 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>{label}</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 15, fontWeight: 600, lineHeight: 1, color: 'success.main' }}>
          {value}
          <Box component="span" sx={{ fontSize: 9, color: 'text.secondary', ml: 0.3, fontWeight: 400 }}>{unit}</Box>
        </Typography>
      </Stack>
      <Slider
        aria-labelledby={id}
        size="small"
        min={min}
        max={max}
        step={step}
        marks={marks}
        value={value}
        onChange={(_event, next) => onChange(Array.isArray(next) ? next[0] : next)}
      />
      {caption && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, lineHeight: 1.25 }}>
          {caption}
        </Typography>
      )}
    </Box>
  )
}
