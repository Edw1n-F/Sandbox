/**
 * What one live run produced (spec §"run"): the numbers, and the explanation.
 *
 * Separate from `ImpactPanel` on purpose. That panel reports the *stored*
 * ledger — what the plan on disk costs, whatever the sliders say. This one
 * reports a *run*: the same ledger recomputed at the rainfall the planner
 * chose, plus the overlays that were just re-rendered and the plain-language
 * paragraph that goes with them. Merging the two would leave a planner
 * looking at one panel and not knowing whether they were reading today's
 * committed figures or the run they just asked for.
 */
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import type { RunResult } from '@/api/types'

/** m3 with thousands separators, rounded: a 12-digit float reads as noise. */
function m3(value: number): string {
  return `${Math.round(value).toLocaleString('en-US')} m³`
}

function ha(value: number): string {
  return `${value.toFixed(1)} ha`
}

/** Signed, so the direction of the change is in the number not in the label. */
function signed(value: number, digits = 0): string {
  const v = value.toFixed(digits)
  return `${value > 0 ? '+' : ''}${v}`
}

/**
 * A delta's colour. Worse-than-today is red, better is green: the opposite of
 * the Chinese market convention, and deliberately so — this is an
 * environmental burden, where red means "more flooding", not "up".
 */
function deltaColor(value: number, downwardIsBetter = true): string {
  if (Math.abs(value) < (downwardIsBetter ? 1 : 0.001)) return 'text.secondary'
  const worse = downwardIsBetter ? value > 0 : value < 0
  return worse ? 'error.main' : 'success.main'
}

/** One mono readout row: label left, value + signed delta right. */
function Row({
  label,
  value,
  delta,
  digits = 0,
}: {
  label: string
  value: string
  delta?: number
  digits?: number
}) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="baseline">
      <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'inherit' }}>
        {label}
      </Typography>
      <Typography className="hud-mono" sx={{ fontSize: 12, color: 'text.primary' }}>
        {value}
        {delta !== undefined && (
          <Box component="span" sx={{ color: deltaColor(delta), ml: 0.75 }}>
            {signed(delta, digits)}
          </Box>
        )}
      </Typography>
    </Stack>
  )
}

interface Props {
  result: RunResult
}

export default function RunSummary({ result }: Props) {
  const { stats, inputs, explanation, explanationSource, interventionsSkipped } =
    result
  const delta = stats.delta
  const skippedNote = interventionsSkipped.length
    ? interventionsSkipped
        .map((s) => `${s.kind}: ${s.why}`)
        .join(' · ')
    : null

  return (
    <HudPanel
      title={`Run ${result.runId.slice(0, 8)}`}
      right={
        <Stack direction="row" spacing={0.5}>
          <Chip size="small" label={`${inputs.rainfallMm.toFixed(0)} mm`} />
          {inputs.heatPreset === 'heatwave' && (
            <Chip size="small" color="warning" label="heatwave display" />
          )}
        </Stack>
      }
    >
      <Typography variant="body2" sx={{ mt: 0.5 }}>
        {explanation}
      </Typography>

      {/* Who wrote that paragraph. A demo with no key must not read as if a
          model did (CLAUDE.md's standing rule about quietly-not-AI features). */}
      <Typography variant="caption" color="text.secondary" display="block">
        {explanationSource === 'template'
          ? 'Assembled from the run’s own numbers; no language model is configured.'
          : `Written by ${explanationSource} from the run’s numbers.`}
      </Typography>

      <Stack spacing={0.5} sx={{ mt: 1.5 }}>
        <Row
          label="Unstored runoff"
          value={m3(stats.scenario.unstoredM3)}
          delta={delta.unstoredM3}
        />
        <Row
          label="Wet area"
          value={ha(stats.scenario.wetAreaHa)}
          delta={delta.wetAreaHa}
          digits={1}
        />
        <Row
          label="Heat-exposure index"
          value={stats.scenario.heatBlendedMean.toFixed(3)}
          delta={delta.heatBlendedMean}
          digits={3}
        />
        <Row
          label="Green · paved"
          value={`${ha(delta.greenAreaM2 / 10_000)} · ${ha(delta.pavedAreaM2 / 10_000)}`}
        />
      </Stack>

      {skippedNote && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
          Not scored: {skippedNote}
        </Typography>
      )}

      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
        {result.warning}
      </Typography>
    </HudPanel>
  )
}
