/**
 * LLM configuration popover (plan v2 §9).
 *
 * Two states:
 * - **Card** -- once a model is configured, the panel opens on a compact card
 *   showing the provider, base URL and model. The API key is never shown.
 * - **Edit form** -- behind the card's "Edit" button (or shown directly on the
 *   first visit when nothing is configured yet).
 *
 * The server persists edited fields to `data/llm_config.json` on save, so a
 * configured model survives a restart; the key field stays write-only and is
 * never echoed back.
 *
 * Status dot semantics match ChatPanel's header dot:
 *   green  = provider configured and reachable
 *   amber  = parser only (no LLM)
 *   grey   = unknown (never queried yet)
 */
import { useEffect, useState } from 'react'

import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Card from '@mui/material/Card'
import CardActions from '@mui/material/CardActions'
import CardContent from '@mui/material/CardContent'
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

/** Which provider the configured card should describe. */
function cardInfo(cfg: LLMConfig) {
  const openai = Boolean(cfg.baseUrl || cfg.model)
  return {
    provider: openai ? 'OpenAI-compatible' : 'Anthropic',
    baseUrl: openai ? cfg.baseUrl : '',
    model: openai ? cfg.model : cfg.anthropicModel || 'claude-sonnet-5',
  }
}

export default function LLMConfigPanel({ onConfiguredChange }: Props) {
  const [cfg, setCfg] = useState<LLMConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Informational note (amber) -- e.g. "saved, but still missing a field". */
  const [notice, setNotice] = useState<string | null>(null)
  /** true = show the edit form; false = show the card (or "not configured"). */
  const [editing, setEditing] = useState(false)

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
      // Never populate key fields from the server -- they are write-only.
      setApiKey('')
      setAnthropicApiKey('')
      // First visit: show the card if something is already configured, else
      // drop straight into the form so a fresh deployment is one click shorter.
      setEditing(!data.configured)
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
    setNotice(null)
    try {
      // Build payload with only the fields the user actually touched. An empty
      // string is still sent (it is how the user clears a value); an untouched
      // key field is left out of the request so an existing key is kept.
      const payload: Record<string, unknown> = {
        baseUrl,
        model,
        anthropicModel,
      }
      if (apiKey) payload.apiKey = apiKey
      if (anthropicApiKey) payload.anthropicApiKey = anthropicApiKey
      const { data } = await http.put<LLMConfig>('/llm-config', payload)
      setCfg(data)
      setApiKey('')
      setAnthropicApiKey('')
      if (data.configured) {
        setEditing(false)
      } else {
        // A key alone is not a complete provider: the OpenAI-compatible path
        // still needs Base URL + Model. Stay on the form and say what is
        // missing instead of collapsing to the bare "No model configured" card.
        const missing: string[] = []
        if (!data.baseUrl) missing.push('Base URL')
        if (!data.model) missing.push('Model')
        setEditing(true)
        setNotice(
          missing.length
            ? `Saved, but ${missing.join(' and ')} ${missing.length > 1 ? 'are' : 'is'} still needed to turn the live model on.`
            : 'Saved. No live provider is configured yet — add an Anthropic API key, or a Base URL + Model.',
        )
      }
      onConfiguredChange?.(data.configured)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const dotColor =
    cfg === null ? '#7d928a' : cfg.configured ? '#5cff9d' : '#ff9d2e'

  // The card's display fields, resolved once. Non-null only when configured.
  const info = cfg?.configured ? cardInfo(cfg) : null

  const textFieldSx = {
    inputProps: { sx: { fontSize: 11.5 } },
    InputLabelProps: { sx: { fontSize: 11.5 } },
  } as const

  return (
    <Box sx={{ p: 1.5, minWidth: 320 }}>
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

      {editing ? (
        <Stack spacing={1}>
          <Typography sx={{ fontSize: 11, color: 'text.secondary' }}>
            OpenAI-compatible endpoint
          </Typography>
          <TextField
            size="small"
            label="Base URL"
            placeholder="https://api.deepseek.com"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={busy}
            {...textFieldSx}
          />
          <TextField
            size="small"
            label="Model"
            placeholder="deepseek-chat"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={busy}
            {...textFieldSx}
          />
          <TextField
            size="small"
            label="API key (write-only)"
            type="password"
            placeholder="leave blank to keep current"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            disabled={busy}
            {...textFieldSx}
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
            {...textFieldSx}
          />
          <TextField
            size="small"
            label="Anthropic API key (write-only)"
            type="password"
            placeholder="leave blank to keep current"
            value={anthropicApiKey}
            onChange={(e) => setAnthropicApiKey(e.target.value)}
            disabled={busy}
            {...textFieldSx}
          />

          {notice && (
            <Typography sx={{ fontSize: 10.5, color: '#ff9d2e' }}>{notice}</Typography>
          )}
          {error && (
            <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>
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
              onClick={() => {
                setNotice(null)
                setEditing(false)
              }}
              sx={{ fontSize: 11 }}
            >
              Cancel
            </Button>
          </Stack>
        </Stack>
      ) : info ? (
        <Card variant="outlined" sx={{ bgcolor: 'background.paper' }}>
          <CardContent sx={{ pb: 1 }}>
            <Typography
              className="hud-label"
              sx={{ fontSize: 10, color: 'text.secondary', mb: 1 }}
            >
              Configured model
            </Typography>
            <Stack spacing={0.5}>
              <Typography className="hud-mono" sx={{ fontSize: 11 }}>
                {info.provider}
              </Typography>
              {info.baseUrl && (
                <Typography className="hud-mono" sx={{ fontSize: 10, color: 'text.secondary' }}>
                  {info.baseUrl}
                </Typography>
              )}
              <Typography sx={{ fontSize: 11, color: '#5cff9d' }}>
                {info.model || '—'}
              </Typography>
            </Stack>
          </CardContent>
          <CardActions sx={{ pt: 0 }}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => {
                setError(null)
                setNotice(null)
                setEditing(true)
              }}
              sx={{ fontSize: 11 }}
            >
              Edit
            </Button>
          </CardActions>
        </Card>
      ) : (
        <Stack spacing={1}>
          <Typography sx={{ fontSize: 11, color: 'text.secondary' }}>
            No model configured — the heuristic parser answers in the meantime.
          </Typography>
          {error && (
            <Typography sx={{ fontSize: 10.5, color: '#ff6b6b' }}>{error}</Typography>
          )}
          <Button
            size="small"
            variant="contained"
            onClick={() => {
              setError(null)
              setNotice(null)
              setEditing(true)
            }}
            sx={{ fontSize: 11, alignSelf: 'flex-start' }}
          >
            Configure
          </Button>
        </Stack>
      )}
    </Box>
  )
}
