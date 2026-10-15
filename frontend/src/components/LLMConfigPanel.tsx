/**
 * LLM configuration popover (plan v2 §9).
 *
 * In-memory overrides only: the server does not write to `.env`, so a restart
 * clears everything the user typed here. The API key field is write-only --
 * the GET never returns it even masked, so refreshing shows the field blank.
 *
 * Status dot semantics match ChatPanel's header dot:
 *   green  = provider configured and reachable
 *   amber  = parser only (no LLM)
 *   grey   = unknown (never queried yet)
 */
import { useEffect, useState } from 'react'

import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Typography from '@mui/material/Typography'

import { http } from '@/api/client'

interface LLMConfig {
  configured: boolean
  anthropicModel: string
  baseUrl: string
  model: string
  jsonMode: string
  allowNoKey: boolean
}

interface Props {
  onConfiguredChange?: (configured: boolean) => void
}

export default function LLMConfigPanel({ onConfiguredChange }: Props) {
  const [cfg, setCfg] = useState<LLMConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** The "Saved." line that fades after a few seconds (plan v2 §9: key is
   *  write-only, so a cleared field is the correct post-save state -- the
   *  user needs an explicit cue that it was saved, not just discarded). */
  const [savedAt, setSavedAt] = useState<number | null>(null)

  // Draft state (what the user types before pressing Save).
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [anthropicApiKey, setAnthropicApiKey] = useState('')
  const [anthropicModel, setAnthropicModel] = useState('')

  const refresh = async () => {
    try {
      const { data } = await http.get<LLMConfig>('/llm-config')
      setCfg(data)
      setBaseUrl(data.baseUrl)
      setModel(data.model)
      setAnthropicModel(data.anthropicModel)
      // Never populate key fields from the server -- they are write-only. The
      // user retypes if they want to change.
      setApiKey('')
      setAnthropicApiKey('')
      onConfiguredChange?.(data.configured)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      // Build payload with only the fields the user actually touched. An empty
      // string is still sent (it is how the user clears a value); an untouched
      // key field is left out of the request.
      const payload: Record<string, unknown> = {
        baseUrl,
        model,
        anthropicModel,
      }
      if (apiKey) payload.apiKey = apiKey
      if (anthropicApiKey) payload.anthropicApiKey = anthropicApiKey
      const { data } = await http.put<LLMConfig>('/llm-config', payload)
      setCfg(data)
      // Key fields are intentionally cleared: the server never echoes a key
      // back (plan v2 §9.2 Decision), so leaving a stale value in the field
      // would misrepresent the live config. The `savedAt` toast is the
      // user-visible confirmation that the clear is a success, not a reset.
      setApiKey('')
      setAnthropicApiKey('')
      setSavedAt(Date.now())
      onConfiguredChange?.(data.configured)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const dotColor =
    cfg === null ? '#7d928a' : cfg.configured ? '#5cff9d' : '#ff9d2e'

  return (
    <Box sx={{ p: 1.5, minWidth: 300 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <Box className="hud-dot" sx={{ color: dotColor }} />
        <Typography className="hud-label" sx={{ fontSize: 10 }}>
          {cfg === null
            ? 'Loading…'
            : cfg.configured
              ? 'LLM: live'
              : 'LLM: parser only'}
        </Typography>
      </Stack>

      <Typography
        className="hud-mono"
        sx={{ fontSize: 9, color: 'text.secondary', mb: 1 }}
      >
        Overrides are in-memory; a restart restores .env.
      </Typography>

      <Stack spacing={1}>
        <Typography sx={{ fontSize: 11, color: 'text.secondary' }}>
          OpenAI-compatible endpoint
        </Typography>
        <TextField
          size="small"
          label="Base URL"
          placeholder="http://localhost:11434/v1"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          disabled={busy}
          inputProps={{ sx: { fontSize: 11.5 } }}
          InputLabelProps={{ sx: { fontSize: 11.5 } }}
        />
        <TextField
          size="small"
          label="Model"
          placeholder="qwen2.5:3b-instruct"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={busy}
          inputProps={{ sx: { fontSize: 11.5 } }}
          InputLabelProps={{ sx: { fontSize: 11.5 } }}
        />
        <TextField
          size="small"
          label="API key (write-only)"
          type="password"
          placeholder="leave blank to keep current"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          disabled={busy}
          inputProps={{ sx: { fontSize: 11.5 } }}
          InputLabelProps={{ sx: { fontSize: 11.5 } }}
        />

        <Typography sx={{ fontSize: 11, color: 'text.secondary', mt: 1 }}>
          Anthropic (optional, takes precedence)
        </Typography>
        <TextField
          size="small"
          label="Anthropic model"
          value={anthropicModel}
          onChange={(e) => setAnthropicModel(e.target.value)}
          disabled={busy}
          inputProps={{ sx: { fontSize: 11.5 } }}
          InputLabelProps={{ sx: { fontSize: 11.5 } }}
        />
        <TextField
          size="small"
          label="Anthropic API key (write-only)"
          type="password"
          placeholder="leave blank to keep current"
          value={anthropicApiKey}
          onChange={(e) => setAnthropicApiKey(e.target.value)}
          disabled={busy}
          inputProps={{ sx: { fontSize: 11.5 } }}
          InputLabelProps={{ sx: { fontSize: 11.5 } }}
        />

        {error && (
          <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>
        )}
        {savedAt && !error && (
          <Typography sx={{ fontSize: 10.5, color: '#5cff9d' }}>
            Saved. Key fields cleared because the server never echoes them back.
          </Typography>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
          <Button
            size="small"
            variant="contained"
            disabled={busy}
            onClick={() => void save()}
            sx={{ fontSize: 11 }}
          >
            {busy ? 'Saving…' : 'Save'}
          </Button>
          <Button
            size="small"
            variant="outlined"
            disabled={busy}
            onClick={() => void refresh()}
            sx={{ fontSize: 11 }}
          >
            Reload
          </Button>
        </Stack>
      </Stack>
    </Box>
  )
}
