/**
 * The left full-height LLM panel.
 *
 * The conversational half of the reorg: after each run it shows the plain-
 * language explanation (already produced by the backend, whether template or
 * model), offers a few clickable intervention suggestions, and accepts a
 * free-form instruction that routes through the existing AI plan pipeline.
 *
 * What it is honest about: with no LLM key configured, the only "AI" available
 * is the deterministic intervention parser, so free-form *questions* about the
 * sandbox cannot be answered and the panel says so rather than fabricating.
 * Suggestions and intervention instructions still work (the parser handles
 * those); Q&A is the one capability that genuinely needs a model.
 */
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { useEffect, useRef, useState } from 'react'

import { errorMessage } from '@/api/client'
import { createAiPlan } from '@/api/planApi'
import { useSceneStore } from '@/state/sceneStore'

interface Message {
  id: string
  role: 'ai' | 'user'
  text: string
  chips?: string[]
}

const SUGGESTIONS = [
  'Add a retention basin north of the canal',
  'Plant trees along the main road',
  'Make the western blocks 25% taller',
]

let nextId = 1
const mkId = () => `m${nextId++}`

/** Very rough: does this look like a question rather than an intervention? */
function looksLikeQuery(text: string): boolean {
  return /\b(why|how|what|which|status|state|explain|compare|better|worse)\b/i.test(text) && !/\b(add|place|plant|fill|remove|rotate|resize|raise|lower|move|scale)\b/i.test(text)
}

export default function ChatPanel() {
  const sceneId = useSceneStore((s) => s.sceneId)
  const runResult = useSceneStore((s) => s.runResult)
  const running = useSceneStore((s) => s.running)

  const [messages, setMessages] = useState<Message[]>([
    {
      id: mkId(),
      role: 'ai',
      text: 'Sandbox loaded. Adjust the rainfall and temperature sliders (top-right), toggle a project, then press Run. After the run I’ll describe what changed and suggest interventions here.',
      chips: SUGGESTIONS,
    },
  ])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null)
  const lastRunId = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  // The model-configured dot. Green = live LLM, amber = parser only, grey = unknown.
  useEffect(() => {
    let cancelled = false
    void fetch('/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`status ${r.status}`))))
      .then((d) => { if (!cancelled) setLlmConfigured(Boolean(d?.llm?.configured)) })
      .catch(() => { if (!cancelled) setLlmConfigured(null) })
    return () => { cancelled = true }
  }, [])

  // After each run, push the explanation as an AI message (once per runId).
  useEffect(() => {
    if (!runResult || running) return
    if (lastRunId.current === runResult.runId) return
    lastRunId.current = runResult.runId
    setMessages((prev) => [
      ...prev,
      {
        id: mkId(),
        role: 'ai',
        text: runResult.explanation +
          (runResult.explanationSource === 'template'
            ? '\n\n_Assembled from the run’s numbers; no language model is configured._'
            : ''),
        chips: SUGGESTIONS,
      },
    ])
  }, [runResult, running])

  // Keep the latest message in view as the thread grows.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || busy || !sceneId) return
    setMessages((prev) => [...prev, { id: mkId(), role: 'user', text: trimmed }])
    setInput('')
    setBusy(true)
    try {
      const r = await createAiPlan(sceneId, trimmed)
      if (r.features > 0 && r.planId) {
        // Selecting the new plan routes the features through the existing store
        // + render path, so the 3D sandbox updates with no special-casing.
        const { usePlanStore } = await import('@/state/planStore')
        await usePlanStore.getState().refreshPlans(sceneId)
        await usePlanStore.getState().selectPlan(r.planId)
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'ai',
            text: `Placed ${r.features} intervention${r.features === 1 ? '' : 's'} (${r.types.join(', ') || 'geometry'}) into the sandbox. Status: draft. Run again to see the effect.`,
            chips: SUGGESTIONS,
          },
        ])
      } else if (looksLikeQuery(trimmed) && !llmConfigured) {
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'ai',
            text: 'I can’t answer free-form questions without a language model configured. Set ANTHROPIC_API_KEY (or LLM_BASE_URL) on the backend to enable Q&A. I can still place interventions — try “add a retention basin near the river”.',
            chips: SUGGESTIONS,
          },
        ])
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: mkId(),
            role: 'ai',
            text: r.message || 'I couldn’t place that as an intervention. Try naming a type (retention basin, trees, green space, water reservoir) and a location.',
            chips: SUGGESTIONS,
          },
        ])
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { id: mkId(), role: 'ai', text: `Error: ${errorMessage(err)}` },
      ])
    } finally {
      setBusy(false)
    }
  }

  const dotColor = llmConfigured === null ? '#7d928a' : llmConfigured ? '#5cff9d' : '#ff9d2e'

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 0,
        left: 0,
        bottom: 0,
        width: 340,
        zIndex: 1100,
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid',
        borderColor: 'divider',
        backgroundColor: 'rgba(6, 10, 9, 0.92)',
        backdropFilter: 'blur(16px)',
      }}
    >
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.5, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Box className="hud-dot" sx={{ color: dotColor }} />
        <Box sx={{ flex: 1 }}>
          <Typography className="hud-label">Assistant</Typography>
          <Typography className="hud-mono" sx={{ fontSize: 9, color: 'text.secondary' }}>
            {llmConfigured === null
              ? 'LLM status unknown'
              : llmConfigured
                ? 'Live model'
                : 'Heuristic parser — not AI'}
          </Typography>
        </Box>
      </Stack>

      {/* Thread */}
      <Box ref={scrollRef} sx={{ flex: 1, overflowY: 'auto', px: 2, py: 2 }}>
        <Stack spacing={1.75}>
          {messages.map((m) => (
            <Box
              key={m.id}
              sx={{
                alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '88%',
                px: 1.5,
                py: 1.25,
                borderRadius: m.role === 'user' ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
                border: '1px solid',
                borderColor: m.role === 'user' ? 'rgba(92,255,157,0.4)' : 'divider',
                bgcolor: m.role === 'user' ? 'rgba(92,255,157,0.08)' : 'rgba(255,255,255,0.03)',
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  fontSize: 12.5,
                  lineHeight: 1.55,
                  whiteSpace: 'pre-wrap',
                  color: 'text.primary',
                  '& em': { color: 'text.secondary', fontStyle: 'italic' },
                }}
              >
                {m.text}
              </Typography>
              {m.chips && (
                <Stack direction="row" spacing={0.5} sx={{ mt: 1, flexWrap: 'wrap', gap: 0.5 }}>
                  {m.chips.map((c) => (
                    <Chip
                      key={c}
                      label={c}
                      size="small"
                      onClick={() => void send(c)}
                      sx={{ cursor: 'pointer' }}
                    />
                  ))}
                </Stack>
              )}
            </Box>
          ))}
          {busy && (
            <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'flex-start' }}>
              working…
            </Typography>
          )}
        </Stack>
      </Box>

      {/* Input */}
      <Box sx={{ p: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
        <Stack direction="row" alignItems="flex-end" spacing={1}>
          <TextField
            fullWidth
            multiline
            maxRows={3}
            placeholder="Describe an intervention or ask a question…"
            value={input}
            disabled={busy || !sceneId}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send(input)
              }
            }}
            sx={{ '& .MuiOutlinedInput-root': { fontSize: 12.5 } }}
          />
          <IconButton
            aria-label="Send"
            disabled={busy || !input.trim() || !sceneId}
            onClick={() => void send(input)}
            sx={{
              width: 32,
              height: 32,
              borderRadius: '8px',
              bgcolor: 'rgba(92,255,157,0.14)',
              border: '1px solid rgba(92,255,157,0.4)',
              color: '#5cff9d',
              '&:hover': { bgcolor: 'rgba(92,255,157,0.28)' },
              '&.Mui-disabled': { borderColor: 'divider', color: 'rgba(125,146,138,0.4)' },
            }}
          >
            <span style={{ fontSize: 14 }}>↑</span>
          </IconButton>
        </Stack>
        <Typography className="hud-mono" sx={{ fontSize: 8.5, color: 'rgba(125,146,138,0.6)', mt: 0.5 }}>
          ENTER TO SEND · SHIFT+ENTER FOR A NEW LINE
        </Typography>
      </Box>
    </Box>
  )
}
