// All requests go to the same origin (/api/v1). No keys or provider URLs live in the browser.
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'
import type { z } from 'zod'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  code: string
  requestId?: string
  details?: unknown
  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.requestId = requestId
    this.details = details
  }
}

function tokenHeader(): Record<string, string> {
  // Optional bearer token for deployments that set API_AUTH_TOKEN; kept for the session only.
  const t = sessionStorage.getItem('api_token')
  return t ? { Authorization: `Bearer ${t}` } : {}
}

export async function api<T = any>(path: string, init: RequestInit & { params?: Record<string, unknown> } = {}): Promise<T> {
  const { params, ...rest } = init
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  const url = `${BASE}${path}${qs.size ? `?${qs}` : ''}`
  const isForm = rest.body instanceof FormData
  let res: Response
  try {
    res = await fetch(url, {
      ...rest,
      headers: { ...(rest.body && !isForm ? { 'Content-Type': 'application/json' } : {}), ...tokenHeader(), ...rest.headers },
    })
  } catch {
    throw new ApiError(0, 'network_error', 'The API could not be reached. Check that the backend is running.')
  }
  if (!res.ok) {
    let body: any = null
    try {
      body = await res.json()
    } catch {
      /* non-JSON error body */
    }
    const e = body?.error
    throw new ApiError(res.status, e?.code ?? 'http_error', e?.message ?? `Request failed (${res.status})`, e?.request_id, e?.details)
  }
  return (await res.json()) as T
}

export const post = <T = any>(path: string, body?: unknown, params?: Record<string, unknown>) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body), params })

/** GET with optional zod validation of the response contract. */
export function useApi<T = any>(
  key: unknown[],
  path: string | null,
  params?: Record<string, unknown>,
  opts?: Partial<UseQueryOptions<T, ApiError>> & { schema?: z.ZodType<T> },
) {
  const { schema, ...rest } = opts ?? {}
  return useQuery<T, ApiError>({
    queryKey: [...key, params ?? null],
    enabled: path !== null,
    queryFn: async () => {
      const data = await api<T>(path as string, { params })
      if (!schema) return data
      const parsed = schema.safeParse(data)
      if (!parsed.success) {
        throw new ApiError(200, 'contract_mismatch', `The API response did not match the expected shape: ${parsed.error.issues[0]?.path.join('.')} ${parsed.error.issues[0]?.message}`)
      }
      return parsed.data
    },
    ...rest,
  })
}

export function useAction<TIn = void, TOut = any>(fn: (input: TIn) => Promise<TOut>, invalidate: unknown[][] = []) {
  const qc = useQueryClient()
  return useMutation<TOut, ApiError, TIn>({
    mutationFn: fn,
    onSuccess: () => {
      for (const k of invalidate) qc.invalidateQueries({ queryKey: k })
    },
  })
}

/** Poll a background job until it leaves the running state. */
export async function waitForJob(jobId: number, onTick?: (status: string) => void): Promise<any> {
  for (let i = 0; i < 600; i++) {
    const j = await api(`/jobs/${jobId}`)
    onTick?.(j.status)
    if (j.status !== 'running') return j
    await new Promise((r) => setTimeout(r, 1000))
  }
  throw new ApiError(0, 'timeout', 'The job is still running. Check its status on the Data status page.')
}
