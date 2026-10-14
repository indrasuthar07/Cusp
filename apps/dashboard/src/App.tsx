import { useEffect, useState } from 'react'
import {
  ApiError,
  getCurrentUser,
  getHealth,
  getProviderProfiles,
  getRuns,
  type DashboardRun,
  type HealthResponse,
  type ProviderProfile,
  type UserIdentity,
} from './api'

type AuthState =
  | { status: 'loading' }
  | { status: 'authenticated'; user: UserIdentity }
  | { status: 'signed-out' }
  | { status: 'error'; message: string }

type Panel = 'overview' | 'runs' | 'providers' | 'policies' | 'audit'

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'loading' })
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [runs, setRuns] = useState<DashboardRun[]>([])
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [activePanel, setActivePanel] = useState<Panel>('overview')

  useEffect(() => {
    let cancelled = false
    void Promise.allSettled([getCurrentUser(), getHealth(), getRuns(), getProviderProfiles()]).then((results) => {
      if (cancelled) return
      const userResult = results[0]
      const healthResult = results[1]
      const runsResult = results[2]
      const profilesResult = results[3]
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
      if (runsResult.status === 'fulfilled') setRuns(runsResult.value.runs)
      if (profilesResult.status === 'fulfilled') setProfiles(profilesResult.value.profiles)
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
              <section className="data-panel run-panel">
                <div className="panel-title-row">
                  <h3>Active runs</h3>
                  <span className="count-badge">{runs.length}</span>
                </div>
                {runs.length === 0 ? <p className="muted">No running sessions in this organization.</p> : (
                  <ul className="data-list">
                    {runs.map((run) => (
                      <li key={run.run_id}>
                        <span><strong>{run.agent_name}</strong><small>{run.run_id}</small></span>
                        <span className="status-pill running">{run.status}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          ) : activePanel === 'runs' ? (
            <section className="data-panel">
              <div className="panel-title-row"><h3>Active runs</h3><span className="count-badge">{runs.length}</span></div>
              {runs.length === 0 ? <p className="muted">No running sessions in this organization.</p> : (
                <ul className="data-list">
                  {runs.map((run) => <li key={run.run_id}><span><strong>{run.agent_name}</strong><small>{run.run_id}</small></span><span className="status-pill running">{run.status}</span></li>)}
                </ul>
              )}
            </section>
          ) : activePanel === 'providers' ? (
            <section className="data-panel">
              <div className="panel-title-row"><h3>Provider profiles</h3><span className="count-badge">{profiles.length}</span></div>
              {profiles.length === 0 ? <p className="muted">No provider profiles configured for this organization.</p> : (
                <div className="profile-grid">
                  {profiles.map((profile) => <article className="profile-card" key={profile.profile_id}><div className="profile-heading"><h3>{profile.name}</h3><span className={profile.is_enabled ? 'status-pill enabled' : 'status-pill disabled'}>{profile.is_enabled ? 'enabled' : 'disabled'}</span></div><p>{profile.provider_kind} · {profile.model_name}</p><small>Updated {new Date(profile.updated_at).toLocaleString()}</small></article>)}
                </div>
              )}
            </section>
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
