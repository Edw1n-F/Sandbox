/**
 * Unified axios wrapper (spec §1.1 `api/client.ts`).
 *
 * The Vite dev server proxies `/api` to `http://localhost:8000`
 * (see vite.config.ts), so the base URL is same-origin and needs no
 * configuration. `VITE_API_BASE_URL` is honoured only if a deployment needs
 * to point somewhere else.
 *
 * The backend returns ONE error envelope (ADR-028) carrying both `detail` and
 * `error`. This module normalises it to a single `ApiError` so callers have one
 * error branch rather than three.
 */
import axios, { AxiosError, type AxiosInstance } from 'axios'

const baseURL = import.meta.env.VITE_API_BASE_URL || '/api'

export const http: AxiosInstance = axios.create({
  baseURL,
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
})

/** A normalised backend failure. `code` is the envelope's machine code. */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: unknown

  constructor(message: string, status: number, code = 'UNKNOWN', details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

function toApiError(error: unknown): ApiError {
  if (!(error instanceof AxiosError)) {
    return new ApiError(
      error instanceof Error ? error.message : 'Unknown error',
      0,
      'NETWORK',
    )
  }

  const status = error.response?.status ?? 0
  const data = error.response?.data as
    | { detail?: string; error?: { code?: string; message?: string; details?: unknown } }
    | undefined

  return new ApiError(
    data?.error?.message ?? data?.detail ?? error.message,
    status,
    data?.error?.code ?? `HTTP_${status}`,
    data?.error?.details,
  )
}

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(toApiError(error)),
)

/** Narrow an unknown catch value to something a React error boundary can show. */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error)
}
