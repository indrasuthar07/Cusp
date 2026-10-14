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
