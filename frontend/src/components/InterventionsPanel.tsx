/**
 * Right-column "Interventions" panel: what the user has placed on the sandbox.
 *
 * Lists the staged plan's pinned rows -- every intervention confirmed from a
 * run's suggestion or from a natural-language instruction. Each row can be
 * deleted, which unpins it: the row leaves the panel, the 3D view and the next
 * run. Delete asks first, because it changes what the next run scores.
 *
 * A value the user supplied and one left to the AI's estimate are labelled
 * differently, so a guessed number never reads as a given one.
 */
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogContentText from '@mui/material/DialogContentText'
import DialogTitle from '@mui/material/DialogTitle'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import Typography from '@mui/material/Typography'
import { useEffect, useState } from 'react'

import HudPanel from '@/components/HudPanel'
import InputCard from '@/components/InputCard'
import { defaultAnswers, fieldError, toValues, type InputAnswers } from '@/components/inputAnswers'
import { errorMessage } from '@/api/client'
import type { StagedFeature } from '@/api/pinsApi'
import { fetchInputs, modifyFeature, type SandboxInput } from '@/api/sandboxApi'
import { useSandboxStore } from '@/state/sandboxStore'
import { useSceneStore } from '@/state/sceneStore'

/** `water_reservoir` -> "Water reservoir". */
const kindLabel = (kind: string) => {
  const words = kind.split('_').join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The values a row carries, each with whether the user gave it or the AI guessed it. */
function valuesOf(feature: StagedFeature): string[] {
  const params = feature.params as {
    storageDepthM?: number
    inputSources?: Record<string, string>
  }
  const out: string[] = []
  if (typeof params.storageDepthM === 'number') {
    const origin = params.inputSources?.storageDepthM === 'user' ? 'your value' : 'AI estimate'
    out.push(`depth ${params.storageDepthM} m · ${origin}`)
  }
  return out
}

function originOf(feature: StagedFeature): string {
  const provenance = (feature.params as { provenance?: { source?: string } }).provenance
  if (provenance?.source === 'suggestion') return 'suggested from the run'
  if (provenance?.source === 'ai-plan') return 'from your instruction'
  return 'added'
}

export default function InterventionsPanel() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const features = useSandboxStore((s) => s.features)
  const error = useSandboxStore((s) => s.error)
  const refresh = useSandboxStore((s) => s.refresh)
  const remove = useSandboxStore((s) => s.remove)

  const [pending, setPending] = useState<StagedFeature | null>(null)
  const [busy, setBusy] = useState(false)
  // Editing one row's values in place: its input card, and the answers so far.
  const [editing, setEditing] = useState<{ id: string; inputs: SandboxInput[]; answers: InputAnswers } | null>(null)
  const [editError, setEditError] = useState<string | null>(null)

  const startEdit = async (f: StagedFeature) => {
    if (!sceneId) return
    setEditError(null)
    const inputs = await fetchInputs(sceneId, f.interventionType, f.params).catch(() => [])
    const answers = defaultAnswers(inputs)
    // Open on what the row holds now: a value the user typed stays theirs.
    const sources = (f.params as { inputSources?: Record<string, string> }).inputSources ?? {}
    for (const input of inputs) {
      if (sources[input.key] === 'user') {
        answers[input.key] = { mode: 'user', text: String((f.params as Record<string, unknown>)[input.key]) }
      }
    }
    setEditing({ id: f.id, inputs, answers })
  }

  const saveEdit = async () => {
    if (!sceneId || !editing) return
    setBusy(true)
    setEditError(null)
    try {
      await modifyFeature(sceneId, editing.id, toValues(editing.inputs, editing.answers))
      await refresh(sceneId)
      setEditing(null)
    } catch (err) {
      setEditError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (sceneId) void refresh(sceneId)
  }, [sceneId, refresh])

  const confirmDelete = async () => {
    if (!sceneId || !pending) return
    setBusy(true)
    await remove(sceneId, pending.id)
    setBusy(false)
    setPending(null)
  }

  return (
    <HudPanel
      title="Interventions"
      right={
        features.length > 0 ? (
          <Typography className="hud-mono" sx={{ fontSize: 9, color: '#5cff9d' }}>
            {features.length} placed
          </Typography>
        ) : undefined
      }
      sx={{ width: '100%' }}
    >
      {features.length === 0 ? (
        <Typography variant="caption" color="text.secondary" display="block">
          Nothing placed yet. Run the simulation, then pick a suggestion under its
          card or describe an intervention in the chat.
        </Typography>
      ) : (
        <Stack spacing={0.75}>
          {features.map((f) => (
            <Box
              key={f.id}
              sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, px: 1.25, py: 0.75 }}
            >
              <Stack direction="row" alignItems="center" justifyContent="space-between">
                <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                  <Box
                    sx={{
                      width: 8, height: 8, borderRadius: '2px', flexShrink: 0,
                      bgcolor: String(f.style?.color ?? '#8250df'),
                    }}
                  />
                  <Typography className="hud-mono" variant="body2" sx={{ fontWeight: 600, fontSize: 12 }} noWrap>
                    {kindLabel(f.interventionType)}
                  </Typography>
                </Stack>
                <Stack direction="row" spacing={0.25}>
                  <IconButton
                    size="small"
                    aria-label={`Edit ${kindLabel(f.interventionType)}`}
                    disabled={busy}
                    onClick={() => void startEdit(f)}
                    sx={{ color: 'text.secondary', '&:hover': { color: '#5cff9d' } }}
                  >
                    <span style={{ fontSize: 13, lineHeight: 1 }}>✎</span>
                  </IconButton>
                  <IconButton
                    size="small"
                    aria-label={`Delete ${kindLabel(f.interventionType)}`}
                    disabled={busy}
                    onClick={() => setPending(f)}
                    sx={{ color: 'text.secondary', '&:hover': { color: '#ff6b6b' } }}
                  >
                    <span style={{ fontSize: 13, lineHeight: 1 }}>✕</span>
                  </IconButton>
                </Stack>
              </Stack>
              <Typography variant="caption" color="text.secondary" display="block" sx={{ fontSize: 10 }}>
                {originOf(f)}
              </Typography>
              {valuesOf(f).map((line) => (
                <Typography key={line} className="hud-mono" sx={{ fontSize: 9.5, color: 'text.secondary' }}>
                  {line}
                </Typography>
              ))}
              {editing?.id === f.id && (
                <Box sx={{ mt: 0.75 }}>
                  <InputCard
                    inputs={editing.inputs}
                    answers={editing.answers}
                    disabled={busy}
                    onChange={(answers) => setEditing({ ...editing, answers })}
                  />
                  {editError && (
                    <Typography sx={{ fontSize: 10, color: '#ff6b6b', mt: 0.5 }}>{editError}</Typography>
                  )}
                  <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
                    {editing.inputs.length > 0 && (
                      <Button
                        size="small"
                        variant="contained"
                        disabled={
                          busy || editing.inputs.some((i) => fieldError(i, editing.answers[i.key]) !== null)
                        }
                        onClick={() => void saveEdit()}
                        sx={{ fontSize: 10.5 }}
                      >
                        {busy ? 'Saving…' : 'Save'}
                      </Button>
                    )}
                    <Button size="small" disabled={busy} onClick={() => setEditing(null)} sx={{ fontSize: 10.5 }}>
                      {editing.inputs.length > 0 ? 'Cancel' : 'Close'}
                    </Button>
                  </Stack>
                </Box>
              )}
            </Box>
          ))}
        </Stack>
      )}
      {error && (
        <Typography variant="caption" sx={{ color: '#ff6b6b', display: 'block', mt: 0.75 }}>
          {error}
        </Typography>
      )}
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1, fontSize: 9.5 }}>
        Run again to score what is placed here together with the toggled projects.
      </Typography>

      <Dialog open={pending !== null} onClose={() => !busy && setPending(null)}>
        <DialogTitle>Delete this intervention?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {pending ? `${kindLabel(pending.interventionType)} will be taken off the sandbox ` : ''}
            and left out of the next run.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPending(null)} disabled={busy}>Cancel</Button>
          <Button color="error" variant="contained" onClick={() => void confirmDelete()} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    </HudPanel>
  )
}
