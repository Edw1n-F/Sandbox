/**
 * Ranked candidate sites per axis — the loop's "suggest" beat.
 *
 * The backend reads land cover, the wet grid and the surveyed building
 * footprints and answers with candidate *parcels* per axis (drainage / heat /
 * runoff), each carrying its evidence, the estimate a run would produce, and a
 * ready-made plan intervention. This panel shows them and pins one into the
 * staged scenario with `POST /api/scenes/{sceneId}/pins`.
 *
 * Three rules the panel keeps, because a planner will argue with these
 * numbers:
 *
 * 1. **No combined score, no "best" badge.** Drainage, heat and runoff are
 *    incommensurable; ordering exists inside an axis only.
 * 2. **The deltas are signed.** `runoffDeltaM3` is shown as "runoff Δ" with
 *    its sign — a negative one means water stands instead of running off, and
 *    taking an absolute value would turn that into an invented gain.
 * 3. **Refusals are shown.** `candidatesRefused` names what the 25%-of-study-
 *    area cap kept out; hiding it would make the list look exhaustive when it
 *    is not.
 *
 * Pinned state lives server-side. The panel keeps only a local set of the
 * feature ids the API has confirmed this session, so the button can stop
 * offering to re-pin a footprint (the backend is idempotent — the same
 * footprint returns `alreadyPinned` — but the UI should not pretend the first
 * click did nothing). Wiring the pin into the run is the lead's step.
 */
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Collapse from '@mui/material/Collapse'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import { useEffect, useState } from 'react'

import HudPanel from '@/components/HudPanel'
import { errorMessage } from '@/api/client'
import {
  fetchSuggestions,
  pinSuggestion,
  type Suggestion,
  type SuggestionsResponse,
} from '@/api/suggestApi'
import {
  DEFAULT_DURATION_HOURS,
  DEFAULT_RAINFALL_MMH,
  useSceneStore,
} from '@/state/sceneStore'

/** The axis colour, matching the ledger's own legend in `interventionLayer.ts`. */
const AXIS_COLOR: Record<string, string> = {
  drainage: '#1f6feb',
  heat: '#2ea043',
  runoff: '#8957e5',
}

const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US')

/** A delta keeps its sign: -0.95 stays -0.95, never 0.95. */
const fmtDelta = (n: number) =>
  `${n > 0 ? '+' : n < 0 ? '-' : ''}${Math.abs(n).toFixed(1)}`

/** `water_reservoir` reads better as "water reservoir" in a planner's HUD. */
const human = (kind: string) => kind.split('_').join(' ')

export default function SuggestionsPanel() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const scene = useSceneStore((s) => s.scene)

  const [data, setData] = useState<SuggestionsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  // Server-side truth: the feature ids this session has pinned. The badge
  // counts confirmed pins, nothing more.
  const [pinnedIds, setPinnedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [pinNote, setPinNote] = useState<Record<string, string>>({})
  const [pinError, setPinError] = useState<Record<string, string>>({})
  const [pinBusy, setPinBusy] = useState<string | null>(null)

  // The storm the user is studying: the same precipitation x duration the run
  // sends. Candidates for a 450 mm storm are not candidates for a 50 mm one.
  const rainfallMm = scene
    ? Number(scene.envParams.precipitation ?? DEFAULT_RAINFALL_MMH) *
      Number(scene.envParams.durationHours ?? DEFAULT_DURATION_HOURS)
    : undefined

  useEffect(() => {
    if (!sceneId) return
    let live = true
    setError(null)
    void fetchSuggestions(sceneId, rainfallMm)
      .then((doc) => {
        if (!live) return
        setData(doc)
        setExpanded(doc.axes[0]?.axis ?? null)
      })
      .catch((err: unknown) => {
        // The 503 carries the regeneration command in its detail; showing that
        // verbatim is more useful than "failed to load".
        if (live) setError(errorMessage(err))
      })
    return () => {
      live = false
    }
  }, [sceneId, rainfallMm])

  const pin = async (key: string, suggestion: Suggestion) => {
    if (!sceneId || pinBusy !== null) return
    setPinBusy(key)
    try {
      // Every intervention in the candidate's plan is pinned, with its
      // geometry verbatim — the pinned footprint must stay the one the
      // estimate was computed from.
      const results = await Promise.all(
        suggestion.plan.interventions.map((f) =>
          pinSuggestion(sceneId, {
            geometry: f.geometry,
            interventionType: f.interventionType,
            params: f.params,
            style: f.style,
          }),
        ),
      )
      if (results.length === 0) {
        setPinError((prev) => ({
          ...prev,
          [key]: 'candidate carries no intervention to pin',
        }))
        return
      }
      setPinnedIds((prev) => {
        const next = new Set(prev)
        for (const r of results) next.add(r.featureId)
        return next
      })
      setPinError((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
      setPinNote((prev) => ({
        ...prev,
        [key]: results.some((r) => r.alreadyPinned) ? 'already pinned' : 'pinned',
      }))
    } catch (err: unknown) {
      setPinNote((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
      setPinError((prev) => ({ ...prev, [key]: errorMessage(err) }))
    } finally {
      setPinBusy(null)
    }
  }

  return (
    <HudPanel
      title="Suggestions"
      right={
        pinnedIds.size > 0 ? (
          <Typography className="hud-mono" sx={{ fontSize: 9, color: '#5cff9d' }}>
            {pinnedIds.size} pinned
          </Typography>
        ) : undefined
      }
      sx={{ width: '100%' }}
    >
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>
        Parcels read from the rasters, ranked within each axis. The axes are
        incommensurable — no total, no best. Pin one to add it to the staged
        scenario.
      </Typography>

      {error && (
        <Typography variant="caption" color="error" display="block" sx={{ mb: 1 }}>
          {error}
        </Typography>
      )}

      {!data && !error && (
        <Typography variant="caption" color="text.secondary">
          Loading candidate sites…
        </Typography>
      )}

      {data?.axes.map((axis) => {
        const isOpen = expanded === axis.axis
        return (
          <Box
            key={axis.axis}
            sx={{
              mb: 0.5,
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              sx={{
                px: 1.25,
                py: 0.75,
                cursor: 'pointer',
                '&:hover': { bgcolor: 'rgba(92,255,157,0.06)' },
              }}
              onClick={() => setExpanded(isOpen ? null : axis.axis)}
            >
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography
                  className="hud-mono"
                  variant="body2"
                  sx={{ fontWeight: 600, fontSize: 12.5 }}
                >
                  {human(axis.axis)}
                </Typography>
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    bgcolor: AXIS_COLOR[axis.axis] ?? '#8b949e',
                  }}
                  title={`axis: ${axis.axis}`}
                />
                <Typography
                  className="hud-mono"
                  variant="caption"
                  sx={{ fontSize: 9, color: 'text.secondary' }}
                >
                  {axis.candidates.length} of {axis.candidatesAvailable} offered
                </Typography>
              </Stack>
              <IconButton
                size="small"
                sx={{ p: 0, color: 'text.secondary' }}
                aria-label={isOpen ? 'Collapse' : 'Expand'}
              >
                <span
                  style={{
                    fontSize: 10,
                    transition: 'transform 0.2s',
                    display: 'inline-block',
                    transform: isOpen ? 'rotate(90deg)' : 'none',
                  }}
                >
                  ▶
                </span>
              </IconButton>
            </Stack>
            <Collapse in={isOpen}>
              <Box sx={{ px: 1.25, pb: 1, pt: 0.25 }}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  display="block"
                  sx={{ mb: 0.75 }}
                >
                  {axis.basis}
                </Typography>
                <Stack spacing={0.5}>
                  {axis.candidates.map((candidate, i) => {
                    const key = `${axis.axis}:${i}`
                    const note = pinNote[key]
                    const pinErr = pinError[key]
                    const busy = pinBusy === key
                    return (
                      <Box
                        key={key}
                        sx={{
                          px: 1,
                          py: 0.6,
                          borderRadius: 1.5,
                          border: '1px solid',
                          borderColor: note ? 'rgba(92,255,157,0.5)' : 'transparent',
                          bgcolor: note ? 'rgba(92,255,157,0.12)' : 'rgba(255,255,255,0.03)',
                        }}
                      >
                        <Stack
                          direction="row"
                          alignItems="center"
                          justifyContent="space-between"
                        >
                          <Stack spacing={0.25}>
                            <Typography
                              className="hud-mono"
                              variant="caption"
                              sx={{ fontSize: 11, color: 'text.primary' }}
                            >
                              {human(candidate.kind)}
                            </Typography>
                            <Typography
                              className="hud-mono"
                              variant="caption"
                              sx={{ fontSize: 9, color: 'text.secondary' }}
                            >
                              {candidate.site.areaHa.toFixed(1)} ha ·{' '}
                              {fmtInt(candidate.site.cells)} cells
                            </Typography>
                          </Stack>
                          {note ? (
                            <Typography
                              className="hud-mono"
                              variant="caption"
                              sx={{ fontSize: 9.5, color: '#5cff9d' }}
                            >
                              {note}
                            </Typography>
                          ) : (
                            <Button
                              size="small"
                              variant="outlined"
                              disabled={busy}
                              onClick={() => void pin(key, candidate)}
                              sx={{ fontSize: 10, minWidth: 0, px: 1 }}
                            >
                              {busy ? 'Pinning…' : 'Pin'}
                            </Button>
                          )}
                        </Stack>
                        <Typography
                          className="hud-mono"
                          variant="caption"
                          sx={{ fontSize: 9, color: 'text.secondary', display: 'block', mt: 0.25 }}
                        >
                          storage {fmtInt(candidate.estimate.storageAddedM3)} m³ · runoff Δ{' '}
                          {fmtDelta(candidate.estimate.runoffDeltaM3)} m³ · heat Δ{' '}
                          {fmtDelta(candidate.estimate.heatIndexDelta)}
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          display="block"
                          sx={{ fontSize: 9.5, lineHeight: 1.4 }}
                          title={candidate.estimate.basis}
                        >
                          {candidate.site.evidence}
                        </Typography>
                        <Typography
                          className="hud-mono"
                          variant="caption"
                          color="text.secondary"
                          display="block"
                          sx={{ fontSize: 8.5, lineHeight: 1.4 }}
                        >
                          basis: {candidate.estimate.basis}
                        </Typography>
                        {pinErr && (
                          <Typography variant="caption" color="error" display="block">
                            {pinErr}
                          </Typography>
                        )}
                      </Box>
                    )
                  })}
                </Stack>
                {axis.candidatesRefused.length > 0 && (
                  <Box sx={{ mt: 0.75 }}>
                    {axis.candidatesRefused.map((refused) => (
                      <Typography
                        key={refused.kind}
                        variant="caption"
                        color="text.secondary"
                        display="block"
                        sx={{ fontSize: 9, lineHeight: 1.4 }}
                      >
                        {human(refused.kind)} — not offered: {refused.why} (
                        {fmtInt(refused.cells)} cells)
                      </Typography>
                    ))}
                  </Box>
                )}
              </Box>
            </Collapse>
          </Box>
        )
      })}

      {data && (
        <Typography
          variant="caption"
          color="text.secondary"
          display="block"
          sx={{ mt: 1, fontSize: 9.5, lineHeight: 1.4 }}
        >
          {data.runContext.rainfallMm} mm storm · study area{' '}
          {data.runContext.siteAreaHa} ha · {data.status}. {data.warning}
        </Typography>
      )}
    </HudPanel>
  )
}
