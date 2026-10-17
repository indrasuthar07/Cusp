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

export type Replay = {
  run: DashboardRun
  actions: Array<{
    action_id: string
    sequence: number
    action_type: string
    provider_profile: string
    progress: Record<string, unknown>
    created_at: string
    decision: {
      verdict: string
      reason: string
      cost_provenance: string
      approved_profile: string | null
      loop_score: number | null
      created_at: string
    } | null
  }>
  audit_events: Array<{
    event_id: string
    event_type: string
    payload: Record<string, unknown>
    created_at: string
  }>
}

export type PolicyVersion = {
  version_id: string
  version_number: number
  rules: Record<string, unknown>
  is_published: boolean
  created_at: string
}

export type PolicySet = {
  policy_set_id: string
  name: string
  schema_version: number
  is_active: boolean
  versions: PolicyVersion[]
}

export type LiveEvent = {
  event_id: string
  stream_id: string
  event_type: string
  session_id: string | null
  payload: Record<string, unknown>
  created_at?: string
}

export type ProviderMetric = {
  provider_kind: string
  model_profile: string
  actions: number
  virtual_cost_micro_usd: number
  provider_cost_micro_usd: number | null
  cost_provenance: string
  avg_latency_ms: number | null
  energy_wh: number | null
  quota_status: string
  health_status: string
}

export type ModelEvaluation = {
  model_profile: string
  provider_kind: string
  samples: number
  average_latency_ms: number | null
  average_virtual_cost_micro_usd: number | null
  average_ev_score: number | null
  average_loop_score: number | null
  evaluation_available: boolean
}

export type ShadowRecommendation = {
  recommendation_id: string
  action_id: string
  model_name: string
  model_version: string
  recommended_verdict: string
  confidence: number
  deterministic_verdict: string
  applied: boolean
  created_at: string
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

export function getRunReplay(runId: string): Promise<Replay> {
  return request<Replay>(`/api/v1/dashboard/runs/${encodeURIComponent(runId)}/replay`)
}

export function intervene(runId: string, action: 'pause' | 'resume' | 'terminate' | 'kill'): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/v1/dashboard/runs/${encodeURIComponent(runId)}/intervention`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
}

export function getPolicies(): Promise<{ policies: PolicySet[] }> {
  return request<{ policies: PolicySet[] }>('/api/v1/dashboard/policies')
}

export function createPolicy(name: string, rules: Record<string, unknown>): Promise<{ policy_set_id: string; version_number: number }> {
  return request<{ policy_set_id: string; version_number: number }>('/api/v1/dashboard/policies', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, rules }),
  })
}

export function createPolicyVersion(policySetId: string, rules: Record<string, unknown>): Promise<{ version_number: number }> {
  return request<{ version_number: number }>(`/api/v1/dashboard/policies/${encodeURIComponent(policySetId)}/versions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rules }),
  })
}

export function publishPolicyVersion(policySetId: string, versionNumber: number): Promise<{ version_number: number }> {
  return request<{ version_number: number }>(`/api/v1/dashboard/policies/${encodeURIComponent(policySetId)}/versions/${versionNumber}/publish`, { method: 'POST' })
}

export function getLiveEvents(after?: string): Promise<{ events: LiveEvent[] }> {
  const query = after ? `?after=${encodeURIComponent(after)}` : ''
  return request<{ events: LiveEvent[] }>(`/api/v1/dashboard/events${query}`)
}

export function getMetrics(): Promise<{ providers: ProviderMetric[]; total_virtual_cost_micro_usd: number; total_provider_cost_micro_usd: number | null; total_energy_wh: number | null }> {
  return request('/api/v1/dashboard/metrics')
}

export function getModelEvaluations(): Promise<{ models: ModelEvaluation[] }> {
  return request<{ models: ModelEvaluation[] }>('/api/v1/dashboard/model-evaluations')
}

export function getShadowRecommendations(): Promise<{ recommendations: ShadowRecommendation[] }> {
  return request<{ recommendations: ShadowRecommendation[] }>('/api/v1/dashboard/shadow-recommendations')
}
