/**
 * Bottom-right stats panel.
 *
 * Compact, self-updating after every run. Reads the live `runResult` (the
 * numbers the last run produced at the chosen rainfall), and falls back to a
 * quiet "run to see stats" state before the first run. Mirrors `RunSummary`'s
 * numbers but trimmed for the corner — the full signed-delta breakdown stays in
 * the chat thread where there is room to read it.
 */
import Box from '@mui/material/Box'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import { useSceneStore } from '@/state/sceneStore'

function m3(value: number): string {
  return Math.round(value).toLocaleString('en-US')
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <Box>
      <Typography className="hud-label" sx={{ fontSize: 8.5, lineHeight: 1.3 }}>
        {label}
      </Typography>
      <Typography
        className="hud-mono"
        sx={{ fontSize: 15, fontWeight: 600, lineHeight: 1.1, color: accent ?? 'text.primary' }}
      >
        {value}
      </Typography>
    </Box>
  )
}

export default function StatsPanel() {
  const runResult = useSceneStore((s) => s.runResult)
  const running = useSceneStore((s) => s.running)

  if (!runResult && !running) {
    return (
      <HudPanel title="Stats" sx={{ width: '100%' }}>
        <Typography variant="caption" color="text.secondary">
          Press <strong style={{ color: '#5cff9d' }}>Run</strong> to compute the
          buffer deficit, wet area and heat index at the chosen rainfall.
        </Typography>
      </HudPanel>
    )
  }

  if (running && !runResult) {
    return (
      <HudPanel title="Stats" sx={{ width: '100%' }}>
        <Typography className="hud-shimmer" variant="caption">
          Computing the buffer deficit, wet area and heat index…
        </Typography>
      </HudPanel>
    )
  }

  if (!runResult) return null
  const { stats, inputs } = runResult
  const delta = stats.delta
  const deltaColor =
    Math.abs(delta.unstoredM3) < 1
      ? 'text.secondary'
      : delta.unstoredM3 > 0
        ? 'error.main'
        : 'success.main'

  const ha = (m2: number) => (m2 / 10_000).toFixed(1)
  const signed = (value: number, unit: string, digits = 0) =>
    `${value > 0 ? '+' : ''}${value.toFixed(digits)}${unit}`
  const scenario = stats.scenario
  const checks = runResult.sanityChecks ?? []
  const tide = stats.tide
  const noInterventions =
    (inputs.planIds?.length ?? 0) > 0 && runResult.interventionsApplied.length === 0

  return (
    <HudPanel
      title="Stats"
      right={
        <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
          {inputs.rainfallMm.toFixed(0)}mm
        </Typography>
      }
      sx={{ width: '100%' }}
    >
      <Stack direction="row" spacing={2} sx={{ mb: 1.25 }}>
        <Stat
          label="Flood extent (≥0.3 m)"
          value={stats.scenario.floodAreaHa != null
            ? `${stats.scenario.floodAreaHa.toFixed(2)} ha`
            : '—'}
          accent="#5cff9d"
        />
      </Stack>
      {tide && tide.active && (
        <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
          <Typography className="hud-label" sx={{ fontSize: 8.5 }}>
            Tide ({tide.label})
          </Typography>
          <Typography className="hud-mono" sx={{ fontSize: 11.5, color: '#1c7ed6' }}>
            +{tide.tailwaterM.toFixed(1)} m backwater
          </Typography>
        </Stack>
      )}
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Δ vs today</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5, color: deltaColor }}>
          {delta.unstoredM3 > 0 ? '+' : ''}{m3(delta.unstoredM3)} m³
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Flood area Δ</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {delta.floodAreaHa != null
            ? `${signed(delta.floodAreaHa, ' ha', 2)}`
            : '—'}
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Ponding area</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {stats.scenario.wetAreaHa.toFixed(1)} ha
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Surface temp (mean)</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {stats.scenario.surfaceMeanC.toFixed(1)} °C
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Air temp (est.)</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {stats.scenario.airMeanC.toFixed(1)} °C
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Δ surface temp</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {signed(delta.surfaceMeanC, ' °C', 1)}
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Green · paved</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {(delta.greenAreaM2 / 10_000).toFixed(1)} · {(delta.pavedAreaM2 / 10_000).toFixed(1)} ha
        </Typography>
      </Stack>

      {/* The three the brief asks for that the panel did not show. */}
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Storage capacity</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {m3(scenario.storageCapacityM3 ?? 0)} m³
          {delta.storageCapacityM3 !== undefined && (
            <span style={{ color: 'text.secondary' }}>
              {' '}({signed(delta.storageCapacityM3, '')})
            </span>
          )}
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Impervious share</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {((scenario.imperviousShare ?? 0) * 100).toFixed(1)} %
          {delta.imperviousShare !== undefined && (
            <span style={{ color: 'text.secondary' }}>
              {' '}({signed(delta.imperviousShare * 100, ' pt', 2)})
            </span>
          )}
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between">
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Green + water</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {ha(scenario.greenWaterAreaM2 ?? 0)} ha
          {delta.greenWaterAreaM2 !== undefined && (
            <span style={{ color: 'text.secondary' }}>
              {' '}({signed(delta.greenWaterAreaM2 / 10_000, ' ha', 1)})
            </span>
          )}
        </Typography>
      </Stack>

      {/* Sanity checks (brief step 3g). Shown, never silently swallowed. */}
      {noInterventions && (
        <Typography variant="caption" display="block" sx={{ mt: 1, fontSize: 9, color: 'warning.main' }}>
          Projects are selected but no intervention reached the grid — see the
          run's skipped list.
        </Typography>
      )}
      {checks.map((c) => (
        <Typography
          key={c.id}
          variant="caption"
          display="block"
          sx={{ mt: 0.5, fontSize: 9, color: 'warning.main' }}
        >
          ⚠ {c.message}
        </Typography>
      ))}

      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1, fontSize: 9 }}>
        {(inputs.planIds?.length ?? 0) > 1
          ? `Cumulative: ${inputs.planIds?.length} projects combined. `
          : ''}
        Surface temp is skin (Landsat), not air; screening figures.
      </Typography>
    </HudPanel>
  )
}
