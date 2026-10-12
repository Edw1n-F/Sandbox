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
import Chip from '@mui/material/Chip'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

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
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="subtitle2">Run {result.runId}</Typography>
        <Stack direction="row" spacing={0.5}>
          <Chip size="small" label={`${inputs.rainfallMm.toFixed(0)} mm`} />
          {inputs.heatPreset === 'heatwave' && (
            <Chip size="small" color="warning" label="heatwave display" />
          )}
        </Stack>
      </Stack>

      <Typography variant="body2" sx={{ mt: 1 }}>
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
        <Stack direction="row" justifyContent="space-between">
          <Typography variant="caption" color="text.secondary">
            Unstored runoff
          </Typography>
          <Typography variant="caption" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {m3(stats.scenario.unstoredM3)}{' '}
            <Typography
              component="span"
              variant="caption"
              sx={{ color: deltaColor(delta.unstoredM3) }}
            >
              ({signed(delta.unstoredM3)} vs today)
            </Typography>
          </Typography>
        </Stack>

        <Stack direction="row" justifyContent="space-between">
          <Typography variant="caption" color="text.secondary">
            Wet area
          </Typography>
          <Typography variant="caption" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {ha(stats.scenario.wetAreaHa)}{' '}
            <Typography
              component="span"
              variant="caption"
              sx={{ color: deltaColor(delta.wetAreaHa) }}
            >
              ({signed(delta.wetAreaHa, 1)})
            </Typography>
          </Typography>
        </Stack>

        <Stack direction="row" justifyContent="space-between">
          <Typography variant="caption" color="text.secondary">
            Heat-exposure index
          </Typography>
          <Typography variant="caption" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {stats.scenario.heatBlendedMean.toFixed(3)}{' '}
            <Typography
              component="span"
              variant="caption"
              sx={{
                color: deltaColor(delta.heatBlendedMean, true),
              }}
            >
              ({signed(delta.heatBlendedMean, 3)})
            </Typography>
          </Typography>
        </Stack>

        <Stack direction="row" justifyContent="space-between">
          <Typography variant="caption" color="text.secondary">
            Green · paved
          </Typography>
          <Typography variant="caption" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {ha(delta.greenAreaM2 / 10_000)} · {ha(delta.pavedAreaM2 / 10_000)}
          </Typography>
        </Stack>
      </Stack>

      {skippedNote && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
          Not scored: {skippedNote}
        </Typography>
      )}

      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
        {result.warning}
      </Typography>
    </Paper>
  )
}
