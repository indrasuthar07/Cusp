import { useEffect, useState } from 'react'
import { ApiError, getCurrentUser, getHealth, type HealthResponse, type UserIdentity } from './api'

type AuthState =
  | { status: 'loading' }
  | { status: 'authenticated'; user: UserIdentity }
  | { status: 'signed-out' }
  | { status: 'error'; message: string }

type Panel = 'overview' | 'runs' | 'providers' | 'policies' | 'audit'

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'loading' })
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [activePanel, setActivePanel] = useState<Panel>('overview')

  useEffect(() => {
    let cancelled = false
    void Promise.allSettled([getCurrentUser(), getHealth()]).then((results) => {
      if (cancelled) return
      const userResult = results[0]
      const healthResult = results[1]
      if (userResult.status === 'rejected') {
        if (userResult.reason instanceof ApiError && (userResult.reason.status === 401 || userResult.reason.status === 403)) {
          setAuth({ status: 'signed-out' })
          return
        }
        setAuth({ status: 'error', message: 'The dashboard could not reach the gateway. Try again shortly.' })
        return
      }
      setAuth({ status: 'authenticated', user: userResult.value })
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value)
    })

    return () => {
      cancelled = true
    }
  }, [])

  if (auth.status === 'loading') {
    return <main className="status-page" aria-live="polite">Checking your Cusp session…</main>
  }

  if (auth.status === 'signed-out') {
    return (
      <main className="status-page">
        <section className="status-card" aria-labelledby="signed-out-title">
          <p className="eyebrow">CUSP CONTROL PLANE</p>
          <h1 id="signed-out-title">Sign in required</h1>
          <p>Your dashboard session is missing or has expired. Sign in through your configured OIDC provider, then return here.</p>
        </section>
      </main>
    )
  }

  if (auth.status === 'error') {
    return (
      <main className="status-page">
        <section className="status-card" role="alert">
          <p className="eyebrow">CUSP CONTROL PLANE</p>
          <h1>Gateway unavailable</h1>
          <p>{auth.message}</p>
        </section>
      </main>
    )
  }

  const { user } = auth
  const navItems: Array<{ id: Panel; label: string }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'runs', label: 'Active runs' },
    { id: 'providers', label: 'Providers' },
    { id: 'policies', label: 'Policies' },
    { id: 'audit', label: 'Audit trail' },
  ]
  const activeLabel = navItems.find((item) => item.id === activePanel)?.label ?? 'Overview'

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">CUSP CONTROL PLANE</p>
          <h1>Operations dashboard</h1>
        </div>
        <div className="identity" aria-label="Signed-in identity">
          <strong>{user.display_name || user.email}</strong>
          <span>{user.email} · {user.role}</span>
        </div>
      </header>

      <div className="workspace">
        <nav className="sidebar" aria-label="Dashboard sections">
          {navItems.map((item) => (
            <button
              className={activePanel === item.id ? 'nav-item active' : 'nav-item'}
              key={item.id}
              onClick={() => setActivePanel(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>

        <main className="content" aria-labelledby="panel-title">
          <div className="content-heading">
            <div>
              <p className="eyebrow">{user.org_public_id}</p>
              <h2 id="panel-title">{activeLabel}</h2>
            </div>
            <span className="session-badge">OIDC session active</span>
          </div>

          {activePanel === 'overview' ? (
            <div className="card-grid">
              <section className="metric-card">
                <span className="metric-label">Gateway</span>
                <strong className={health?.status === 'ok' ? 'healthy' : 'warning'}>{health?.status ?? 'unknown'}</strong>
                <p>Authenticated API control plane</p>
              </section>
              <section className="metric-card">
                <span className="metric-label">Database</span>
                <strong className={health?.database.status === 'ok' ? 'healthy' : 'warning'}>{health?.database.status ?? 'unknown'}</strong>
                <p>{health?.database.latency_ms == null ? 'Latency unavailable' : `${health.database.latency_ms} ms`}</p>
              </section>
              <section className="metric-card">
                <span className="metric-label">Redis</span>
                <strong className={health?.redis.status === 'ok' ? 'healthy' : 'warning'}>{health?.redis.status ?? 'unknown'}</strong>
                <p>{health?.redis.latency_ms == null ? 'Latency unavailable' : `${health.redis.latency_ms} ms`}</p>
              </section>
            </div>
          ) : (
            <section className="empty-panel">
              <h3>{activeLabel}</h3>
              <p>This authenticated surface is ready for the next Phase 6 slice.</p>
            </section>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
