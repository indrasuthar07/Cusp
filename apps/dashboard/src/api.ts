export type UserIdentity = {
  user_public_id: string
  email: string
  display_name: string
  org_public_id: string
  role: string
}

export type ComponentHealth = {
  status: 'ok' | 'down' | string
  latency_ms: number | null
  error: string | null
}

export type HealthResponse = {
  status: 'ok' | 'degraded' | string
  version: string
  database: ComponentHealth
  redis: ComponentHealth
  timestamp: number
}

export type DashboardRun = {
  run_id: string
  status: 'pending' | 'running' | 'paused' | 'completed' | 'terminated' | 'failed' | string
  budget_cap_micro_usd: number
  agent_id: string
  agent_name: string
  started_at: string
  ended_at: string | null
}

export type ProviderProfile = {
  profile_id: string
  name: string
  provider_kind: string
  model_name: string
  capabilities: Record<string, unknown>
  cost_config: Record<string, unknown>
  is_enabled: boolean
  updated_at: string
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: string }
      if (typeof body.detail === 'string' && body.detail.length > 0) message = body.detail
    } catch {
      // Keep the generic message when the response is not JSON.
    }
    throw new ApiError(response.status, message)
  }

  return (await response.json()) as T
}

export function getCurrentUser(): Promise<UserIdentity> {
  return request<UserIdentity>('/api/v1/auth/me/user')
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

export function getRuns(): Promise<{ runs: DashboardRun[]; count: number }> {
  return request<{ runs: DashboardRun[]; count: number }>('/api/v1/dashboard/runs?status=running')
}

export function getProviderProfiles(): Promise<{ profiles: ProviderProfile[]; count: number }> {
  return request<{ profiles: ProviderProfile[]; count: number }>('/api/v1/dashboard/provider-profiles')
}
