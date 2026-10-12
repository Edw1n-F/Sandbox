/**
 * The one question box.
 *
 * The plain-language intervention input, floating over the 3D view as a pill —
 * the reference film's central move: the map is the screen, and the prompt sits
 * on it. Owns the whole ask-a-model round trip (health check, generate,
 * refresh, select) that used to live in `ScenePanel`, so the panel stays
 * read-only reporting and everything conversational lives here.
 */
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { useEffect, useRef, useState } from 'react'
import IconButton from '@mui/material/IconButton'

import { errorMessage } from '@/api/client'
import { createAiPlan, type AiPlanResult } from '@/api/planApi'
import { usePlanStore } from '@/state/planStore'
import { useSceneStore } from '@/state/sceneStore'

const EXAMPLES = [
  'Add a retention basin north of the canal',
  'Make the western blocks 30% taller',
  'Plant trees along the main road',
]

/** Dot colour for the model-configured badge. */
function statusColor(configured: boolean | null): string {
  if (configured === null) return '#7d928a'
  return configured ? '#5cff9d' : '#ff9d2e'
}

export default function PromptBar() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const selectPlan = usePlanStore((s) => s.selectPlan)
  const refreshPlans = usePlanStore((s) => s.refreshPlans)

  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<AiPlanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [focused, setFocused] = useState(false)
  // Tri-state, as in the panel it came from: null means "we could not ask".
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetch('/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`status ${r.status}`))))
      .then((d) => {
        if (!cancelled) setLlmConfigured(Boolean(d?.llm?.configured))
      })
      .catch(() => {
        if (!cancelled) setLlmConfigured(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const submit = async () => {
    const text = instruction.trim()
    if (!text || busy || !sceneId) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await createAiPlan(sceneId, text)
      setResult(r)
      if (r.planId) {
        // List first, then select: the panel derives the plan's label from the
        // list, so the reverse order renders "No plan" beside a populated count.
        await refreshPlans(sceneId)
        await selectPlan(r.planId)
        setInstruction('')
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const disabled = !sceneId || busy

  return (
    <Box
      sx={{
        position: 'absolute',
        bottom: 22,
        left: '50%',
        transform: 'translateX(-50%)',
        width: 'min(580px, calc(100vw - 48px))',
        zIndex: 1200,
      }}
    >
      {/* Ask-example chips, shown while the box is untouched — the film's
          suggestion row. They fill the input rather than submitting directly,
          so the planner edits before committing. */}
      {!instruction && !result && !busy && (
        <Stack
          direction="row"
          spacing={1}
          justifyContent="center"
          sx={{ mb: 1, flexWrap: 'wrap', display: { xs: 'none', md: 'flex' } }}
        >
          {EXAMPLES.map((ex) => (
            <Chip
              key={ex}
              label={ex}
              onClick={() => inputRef.current?.focus()}
              onClickCapture={() => setInstruction(ex)}
              sx={{ cursor: 'pointer', bgcolor: 'rgba(10,16,14,0.8)', backdropFilter: 'blur(10px)' }}
            />
          ))}
        </Stack>
      )}

      {/* What just happened: one line, machine-attributed per ADR-030. */}
      {(result || error) && (
        <Typography
          className="hud-mono"
          sx={{
            fontSize: 10.5,
            color: error ? 'error.main' : 'text.secondary',
            textAlign: 'center',
            mb: 0.75,
            textShadow: '0 1px 6px rgba(0,0,0,0.9)',
          }}
        >
          {error ??
            `${result!.provider} · ${result!.features} feature${result!.features === 1 ? '' : 's'}${
              result!.types.length ? ` · ${result!.types.join(', ')}` : ''
            } · status=draft`}
        </Typography>
      )}

      <Box
        sx={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 1,
          p: 1,
          pl: 1.5,
          borderRadius: '10px',
          border: '1px solid',
          borderColor: focused ? 'rgba(92,255,157,0.5)' : 'divider',
          backgroundColor: 'rgba(8, 13, 12, 0.88)',
          backdropFilter: 'blur(16px)',
          boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
          transition: 'border-color 0.15s',
        }}
      >
        {/* Model-configured dot: green = live model, amber = parser only,
            grey = unknown (backend unreachable). Never claims AI it has not got. */}
        <Stack alignItems="center" sx={{ pt: 1.25, color: statusColor(llmConfigured) }}>
          <Box className="hud-dot" title={
            llmConfigured === null
              ? 'LLM status unknown — /health unreachable'
              : llmConfigured
                ? 'Live LLM configured'
                : 'No LLM key — deterministic parser'
          } />
        </Stack>

        <TextField
          fullWidth
          multiline
          maxRows={4}
          inputRef={inputRef}
          placeholder="Describe an intervention…"
          value={instruction}
          disabled={disabled}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
          sx={{
            '& .MuiOutlinedInput-root': { border: 'none', backgroundColor: 'transparent' },
            '& fieldset': { border: 'none !important' },
            '& textarea': { fontSize: 13 },
          }}
        />

        <IconButton
          aria-label="Generate the intervention"
          disabled={disabled || !instruction.trim()}
          onClick={() => void submit()}
          sx={{
            width: 30,
            height: 30,
            borderRadius: '8px',
            backgroundColor: 'rgba(92,255,157,0.14)',
            border: '1px solid rgba(92,255,157,0.4)',
            color: '#5cff9d',
            '&:hover': { backgroundColor: 'rgba(92,255,157,0.28)' },
            '&.Mui-disabled': { border: '1px solid', borderColor: 'divider', color: 'rgba(125,146,138,0.4)' },
          }}
        >
          {busy ? '···' : '↑'}
        </IconButton>
      </Box>

      <Typography
        className="hud-mono"
        sx={{ fontSize: 9, color: 'rgba(125,146,138,0.6)', textAlign: 'center', mt: 0.5 }}
      >
        machine-proposed · draft · enter to send, shift+enter for a new line
      </Typography>
    </Box>
  )
}
