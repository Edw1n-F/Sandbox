/**
 * What the plan costs, in the units the brief actually asks for.
 *
 * Read-only by design. It calls four reporting endpoints and renders what they
 * say. It issues no edits and holds no state anything else depends on, so it
 * cannot interfere with the controls beside it -- which matters while the rest of
 * this layer is being refactored in parallel.
 *
 * The brief's glossary calls Buffer Deficit "our single headline number, in cubic
 * metres: how much flood water has nowhere to go", and Delta "the change between
 * two runs... we report 'how much worse than today', not absolute values". That is
 * what this shows, first.
 *
 * Everything carries its own limits. The sea-level section in particular reports
 * a *null* result -- none of the official Hong Kong projections move the deficit
 * here -- and says why. A panel that hid that would tell a planner the opposite of
 * what the model found.
 */
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Collapse from '@mui/material/Collapse'
import Link from '@mui/material/Link'
import Paper from '@mui/material/Paper'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import { useEffect, useState } from 'react'

import {
  getLedger,
  getPublishedScenarios,
  getSeaLevel,
  type Ledger,
  type PublishedScenarios,
  type SeaLevelReport,
} from '@/api/impactApi'

const m3 = (n: number) => `${Math.round(n).toLocaleString()} m³`
const ha = (n: number) => `${n.toFixed(1)} ha`

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography variant="overline" color="text.secondary">
        {title}
      </Typography>
      {children}
    </Box>
  )
}

export default function ImpactPanel({ sceneId }: { sceneId: string | null }) {
  const [ledger, setLedger] = useState<Ledger | null>(null)
  const [sea, setSea] = useState<SeaLevelReport | null>(null)
  const [published, setPublished] = useState<PublishedScenarios | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    if (!sceneId) return
    let live = true
    setError(null)
    // Each endpoint answers independently, so one failure must not blank the
    // other three: the overlays may simply not be generated on this machine, and
    // that is a 422 carrying an explanation rather than a blank panel.
    void Promise.allSettled([
      getLedger(sceneId),
      getSeaLevel(sceneId),
      getPublishedScenarios(sceneId),
    ]).then(([l, s, p]) => {
      if (!live) return
      if (l.status === 'fulfilled') setLedger(l.value)
      if (s.status === 'fulfilled') setSea(s.value)
      if (p.status === 'fulfilled') setPublished(p.value)
      const failed = [l, s, p].filter((r) => r.status === 'rejected')
      if (failed.length === 3) {
        const reason = (failed[0] as PromiseRejectedResult).reason as {
          response?: { data?: { detail?: string } }
        }
        setError(reason?.response?.data?.detail ?? String(reason))
      }
    })
    return () => {
      live = false
    }
  }, [sceneId])

  if (!sceneId) return null

  return (
    <Paper elevation={0} variant="outlined" sx={{ p: 1.5 }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ cursor: 'pointer' }}
        onClick={() => setOpen((o) => !o)}
      >
        <Typography variant="subtitle2">Impact — the buffer deficit</Typography>
        <Typography variant="caption" color="text.secondary">
          {open ? 'hide' : 'show'}
        </Typography>
      </Stack>

      <Collapse in={open}>
        {error && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            {error}
          </Alert>
        )}

        {ledger && (
          <>
            <Section title="Today, against this plan">
              <Typography variant="h6" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {m3(ledger.bufferDeficitM3)}
              </Typography>
              <Typography variant="caption" color="text.secondary" display="block">
                buffer deficit — runoff with nowhere to go
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} flexWrap="wrap">
                <Chip
                  size="small"
                  color={ledger.worsens ? 'warning' : 'success'}
                  label={
                    ledger.deltaUnstoredM3 === 0
                      ? 'no change'
                      : `${ledger.deltaUnstoredM3 > 0 ? '+' : '−'}${m3(
                          Math.abs(ledger.deltaUnstoredM3),
                        )} ${ledger.worsens ? 'worse' : 'better'} than today`
                  }
                />
                <Chip size="small" variant="outlined"
                  label={`${ledger.interventionsApplied.length} applied`} />
                {ledger.interventionsSkipped.length > 0 && (
                  <Chip size="small" variant="outlined"
                    label={`${ledger.interventionsSkipped.length} not counted`} />
                )}
              </Stack>
            </Section>

            {ledger.interventionsSkipped.length > 0 && (
              <Section title="Not counted, and why">
                {ledger.interventionsSkipped.map((s, i) => (
                  <Typography key={`${s.id}-${i}`} variant="caption" display="block"
                    color="text.secondary">
                    <strong>{s.kind}</strong> — {s.why}
                  </Typography>
                ))}
              </Section>
            )}
          </>
        )}

        {sea && (
          <Section title="Sea level, 2100 (Hong Kong Observatory)">
            <Typography variant="body2">
              Ground runs {sea.groundElevationM.min}–{sea.groundElevationM.max} m; the
              current water level is {sea.currentWaterLevelM} m.
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} flexWrap="wrap">
              {sea.projections.map((p) => (
                <Chip
                  key={p.id}
                  size="small"
                  variant="outlined"
                  label={`+${p.seaLevelM.toFixed(2)} m → ${
                    p.deltaUnstoredM3 === 0
                      ? 'no change'
                      : `${p.deltaUnstoredM3 > 0 ? '+' : '−'}${m3(
                          Math.abs(p.deltaUnstoredM3),
                        )}`
                  }`}
                />
              ))}
            </Stack>
            <Typography variant="caption" color="text.secondary" display="block"
              sx={{ mt: 0.5 }}>
              {sea.finding}
            </Typography>
          </Section>
        )}

        {published && (
          <Section title="Published programmes, run through this model">
            <Typography variant="caption" color="text.secondary" display="block">
              Source: {published.source.publisher}, retrieved{' '}
              {published.source.retrieved}. Study area {ha(published.studyAreaHa)}.
            </Typography>
            {published.scenarios.map((s) => (
              <Box key={s.scenarioId} sx={{ mt: 0.5 }}>
                <Typography variant="body2">
                  {s.label} — {ha(s.worksAreaHa)} published →{' '}
                  <strong>
                    {s.deltaUnstoredM3 > 0 ? '+' : '−'}
                    {m3(Math.abs(s.deltaUnstoredM3))}
                  </strong>
                </Typography>
                {s.areaShortfallHa > 0 && (
                  <Typography variant="caption" color="warning.main" display="block">
                    {ha(s.areaShortfallHa)} of the programme does not fit the land
                    available, so this figure is capped and understates the effect.
                  </Typography>
                )}
              </Box>
            ))}
            <Link
              href={published.source.url}
              target="_blank"
              rel="noreferrer"
              variant="caption"
              display="block"
              sx={{ mt: 0.5 }}
            >
              {published.source.name}
            </Link>
          </Section>
        )}

        {ledger && (
          <Typography variant="caption" color="text.secondary" display="block"
            sx={{ mt: 1.5 }}>
            {ledger.note}
          </Typography>
        )}
      </Collapse>
    </Paper>
  )
}
