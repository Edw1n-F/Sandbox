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
        <Typography variant="caption" color="text.secondary">Computing…</Typography>
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
          label="Unstored runoff"
          value={`${m3(stats.scenario.unstoredM3)} m³`}
          accent="#5cff9d"
        />
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Δ vs today</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5, color: deltaColor }}>
          {delta.unstoredM3 > 0 ? '+' : ''}{m3(delta.unstoredM3)} m³
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Wet area</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {stats.scenario.wetAreaHa.toFixed(1)} ha
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Heat index</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {stats.scenario.heatBlendedMean.toFixed(3)}
        </Typography>
      </Stack>
      <Stack direction="row" justifyContent="space-between">
        <Typography className="hud-label" sx={{ fontSize: 8.5 }}>Green · paved</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11.5 }}>
          {(delta.greenAreaM2 / 10_000).toFixed(1)} · {(delta.pavedAreaM2 / 10_000).toFixed(1)} ha
        </Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1, fontSize: 9 }}>
        {inputs.heatPreset === 'heatwave' ? 'Heatwave display preset. ' : ''}
        Screening figures, not a drainage simulation.
      </Typography>
    </HudPanel>
  )
}
