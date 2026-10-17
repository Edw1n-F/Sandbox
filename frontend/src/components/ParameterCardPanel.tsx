/**
 * The screening parameter card: what a reader needs *before* trusting a result.
 *
 * The brief's §2 table — site extent, land cover, the inputs the run assumed,
 * and the rows that matter most: **the things this project does not know.**
 * Soil group, heat preset and tree cover carry no value and say why; they are
 * not zeros. The difference between "we did not measure this" and "we forgot to
 * include it" is exactly what this panel keeps visible.
 *
 * ## Why it is collapsed by default
 *
 * It is a reference table, not a status display. The panels above it answer
 * "what is my scenario doing"; this one answers "what should I distrust about
 * the answer". Opening it costs a request and a screenful, so it opens on
 * demand — but it is always one click away rather than only reachable by a
 * client with curl, which is what it was until 18 Oct.
 *
 * ## Nothing here is a verdict
 *
 * Every row carries a `confidence` enum — `MEASURED`, `DERIVED`, `ASSUMPTION`,
 * `UNAVAILABLE` — which says how well the number is *known*, never how good an
 * option is. There is no overall score, no grade, and no ordering across axes:
 * the rows are independent and the card is a list, not a judgement.
 */
import { useEffect, useState } from 'react'

import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'

import HudPanel from '@/components/HudPanel'
import { fetchParameterCard, type ParameterCard } from '@/api/sandboxApi'
import { useSceneStore } from '@/state/sceneStore'

/** Colour a confidence enum. Never a judgement of the option, only of the number. */
function confidenceColour(confidence: string): string {
  switch (confidence) {
    case 'MEASURED':
      return '#5cff9d'
    case 'DERIVED':
      return '#8fd3ff'
    case 'ASSUMPTION':
      return '#ff9d2e'
    default:
      return 'rgba(255,107,107,0.9)'
  }
}

/** A value with its unit, or an explicit statement that there is none. */
function formatValue(entry: ParameterCard['entries'][number]): string {
  if (entry.value === null || entry.value === undefined) return 'not available'
  const rounded = Math.abs(entry.value) >= 100 ? Math.round(entry.value).toLocaleString() : entry.value
  return `${rounded}${entry.unit ? ` ${entry.unit}` : ''}`
}

function Row({ entry }: { entry: ParameterCard['entries'][number] }) {
  const colour = confidenceColour(entry.confidence)
  return (
    <Box sx={{ py: 0.5, borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={1}>
        <Typography sx={{ fontSize: 10.5, lineHeight: 1.3 }}>{entry.label}</Typography>
        <Typography className="hud-mono" sx={{ fontSize: 11, fontWeight: 600, color: colour, whiteSpace: 'nowrap' }}>
          {formatValue(entry)}
        </Typography>
      </Stack>
      <Typography className="hud-mono" sx={{ fontSize: 8.5, lineHeight: 1.35, color: colour }}>
        {entry.confidence}
        {entry.date ? ` · ${entry.date}` : ''}
      </Typography>
      {entry.source && (
        <Typography className="hud-mono" sx={{ fontSize: 8.5, lineHeight: 1.4, color: 'text.secondary' }}>
          {entry.source}
        </Typography>
      )}
      {entry.note && (
        <Typography sx={{ fontSize: 9, lineHeight: 1.4, color: 'text.secondary', mt: 0.25 }}>
          {entry.note}
        </Typography>
      )}
    </Box>
  )
}

export default function ParameterCardPanel() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const [card, setCard] = useState<ParameterCard | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetched on open, not on mount: the card is a reference table, and loading
  // it for every session would spend a request on a panel most sessions never
  // open. A second open re-reads, so the card never shows a stale scenario.
  useEffect(() => {
    if (!open || !sceneId) return
    let live = true
    setBusy(true)
    setError(null)
    void fetchParameterCard(sceneId)
      .then((next) => {
        if (live) setCard(next)
      })
      .catch((err: unknown) => {
        if (live) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (live) setBusy(false)
      })
    return () => {
      live = false
    }
  }, [open, sceneId])

  return (
    <HudPanel title="Parameter card" sx={{ width: '100%' }}>
      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
        <Typography sx={{ fontSize: 9.5, color: 'text.secondary', lineHeight: 1.35 }}>
          The numbers behind a result — including what this project does not know.
        </Typography>
        <Button
          size="small"
          variant={open ? 'outlined' : 'contained'}
          onClick={() => setOpen((o) => !o)}
          sx={{ fontSize: 10, py: 0.25, minWidth: 0, whiteSpace: 'nowrap' }}
        >
          {busy ? 'Reading…' : open ? 'Hide' : 'Show'}
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mt: 0.75, py: 0.25, fontSize: 10 }}>
          {error}
        </Alert>
      )}

      {open && card && (
        <Box sx={{ mt: 0.5 }}>
          {card.warning && (
            <Typography sx={{ fontSize: 9, lineHeight: 1.4, color: 'rgba(255,157,46,0.9)' }}>
              {card.warning}
            </Typography>
          )}
          <Typography className="hud-mono" sx={{ fontSize: 8.5, color: 'text.secondary', lineHeight: 1.4, mt: 0.25 }}>
            {card.status}
            {card.planId ? ` · plan ${card.planId.slice(0, 8)}` : ' · no plan (today’s land cover)'}
          </Typography>
          <Box sx={{ mt: 0.5 }}>
            {card.entries.map((entry) => (
              <Row key={entry.key} entry={entry} />
            ))}
          </Box>
        </Box>
      )}
    </HudPanel>
  )
}
