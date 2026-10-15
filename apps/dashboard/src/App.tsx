import { useEffect, useState } from 'react'
import {
  ApiError,
  getCurrentUser,
  getHealth,
  getProviderProfiles,
  getPolicies,
  getLiveEvents,
  getMetrics,
  getModelEvaluations,
  createPolicyVersion,
  createPolicy,
  publishPolicyVersion,
  getRunReplay,
  intervene,
  getRuns,
  type DashboardRun,
  type HealthResponse,
  type ProviderProfile,
  type PolicySet,
  type LiveEvent,
  type ProviderMetric,
  type ModelEvaluation,
  type Replay,
  type UserIdentity,
} from './api'

type AuthState =
  | { status: 'loading' }
  | { status: 'authenticated'; user: UserIdentity }
  | { status: 'signed-out' }
  | { status: 'error'; message: string }

type Panel = 'overview' | 'runs' | 'providers' | 'metrics' | 'evaluations' | 'policies' | 'audit'

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'loading' })
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [runs, setRuns] = useState<DashboardRun[]>([])
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [replay, setReplay] = useState<Replay | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [policies, setPolicies] = useState<PolicySet[]>([])
  const [policyJson, setPolicyJson] = useState('{"default_action":"pause"}')
  const [policyName, setPolicyName] = useState('Default policy')
  const [policyMessage, setPolicyMessage] = useState('')
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([])
  const [liveStatus, setLiveStatus] = useState<'connecting' | 'connected' | 'reconnecting'>('connecting')
  const [metrics, setMetrics] = useState<{ providers: ProviderMetric[]; total_virtual_cost_micro_usd: number; total_provider_cost_micro_usd: number | null; total_energy_wh: number | null } | null>(null)
  const [evaluations, setEvaluations] = useState<ModelEvaluation[]>([])
  const [activePanel, setActivePanel] = useState<Panel>('overview')

  useEffect(() => {
    let cancelled = false
    void Promise.allSettled([getCurrentUser(), getHealth(), getRuns(), getProviderProfiles(), getPolicies(), getMetrics(), getModelEvaluations()]).then((results) => {
      if (cancelled) return
      const userResult = results[0]
      const healthResult = results[1]
      const runsResult = results[2]
      const profilesResult = results[3]
      const policiesResult = results[4]
      const metricsResult = results[5]
      const evaluationsResult = results[6]
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
      if (policiesResult.status === 'fulfilled') setPolicies(policiesResult.value.policies)
      if (metricsResult.status === 'fulfilled') setMetrics(metricsResult.value)
      if (evaluationsResult.status === 'fulfilled') setEvaluations(evaluationsResult.value.models)
    })

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (auth.status !== 'authenticated') return
    let stopped = false
    let socket: WebSocket | null = null
    let retryTimer: number | undefined
    let lastCreatedAt: string | undefined
    const mergeEvents = (incoming: LiveEvent[]) => {
      if (incoming.length > 0) lastCreatedAt = incoming[incoming.length - 1].created_at ?? lastCreatedAt
      setLiveEvents((current) => {
        const merged = new Map(current.map((event) => [event.event_id, event]))
        incoming.forEach((event) => merged.set(event.event_id, event))
        return Array.from(merged.values()).slice(-100)
      })
    }
    const synchronize = () => getLiveEvents(lastCreatedAt).then((response) => mergeEvents(response.events)).catch(() => undefined)
    const connect = () => {
      if (stopped) return
      setLiveStatus('connecting')
      socket = new WebSocket(`${window.location.origin.replace(/^http/, 'ws')}/api/v1/dashboard/live`)
      socket.onopen = () => {
        setLiveStatus('connected')
        socket?.send(JSON.stringify({ last_event_id: '$' }))
      }
      socket.onmessage = (message) => {
        try { mergeEvents([JSON.parse(message.data) as LiveEvent]) } catch { /* Ignore malformed server frames. */ }
      }
      socket.onclose = () => {
        if (stopped) return
        setLiveStatus('reconnecting')
        void synchronize()
        retryTimer = window.setTimeout(connect, 2000)
      }
      socket.onerror = () => socket?.close()
    }
    void synchronize().finally(connect)
    return () => { stopped = true; if (retryTimer) window.clearTimeout(retryTimer); socket?.close() }
  }, [auth.status])

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
    { id: 'metrics', label: 'Cost & health' },
    { id: 'evaluations', label: 'Model evaluation' },
    { id: 'policies', label: 'Policies' },
    { id: 'audit', label: 'Audit trail' },
  ]
  const activeLabel = navItems.find((item) => item.id === activePanel)?.label ?? 'Overview'
  const loadReplay = (runId: string) => {
    setSelectedRunId(runId)
    void getRunReplay(runId).then(setReplay)
  }
  const sendIntervention = (action: 'pause' | 'resume' | 'terminate' | 'kill') => {
    if (!selectedRunId) return
    void intervene(selectedRunId, action).then(() => {
      void getRuns().then((response) => setRuns(response.runs))
      void getRunReplay(selectedRunId).then(setReplay)
    })
  }
  const updatePolicyVersion = () => {
    const policy = policies[0]
    if (!policy || !['owner', 'admin'].includes(user.role)) return
    try {
      const rules = JSON.parse(policyJson) as Record<string, unknown>
      void createPolicyVersion(policy.policy_set_id, rules).then(() => getPolicies()).then((response) => { setPolicies(response.policies); setPolicyMessage('Draft version created.') }).catch(() => setPolicyMessage('Could not create policy draft.'))
    } catch { setPolicyMessage('Policy JSON is invalid.') }
  }
  const createPolicySet = () => {
    if (!['owner', 'admin'].includes(user.role)) return
    try {
      const rules = JSON.parse(policyJson) as Record<string, unknown>
      void createPolicy(policyName, rules).then(() => getPolicies()).then((response) => { setPolicies(response.policies); setPolicyMessage('Policy draft created.') }).catch(() => setPolicyMessage('Could not create policy.'))
    } catch { setPolicyMessage('Policy JSON is invalid.') }
  }
  const publishLatestPolicy = () => {
    const policy = policies[0]
    const latest = policy?.versions.find((version) => !version.is_published)
    if (!policy || !latest || !['owner', 'admin'].includes(user.role)) return
    void publishPolicyVersion(policy.policy_set_id, latest.version_number).then(() => getPolicies()).then((response) => { setPolicies(response.policies); setPolicyMessage(`Version ${latest.version_number} published.`) }).catch(() => setPolicyMessage('Could not publish policy version.'))
  }
  const renderRunList = () => runs.length === 0 ? <p className="muted">No running sessions in this organization.</p> : (
    <ul className="data-list">
      {runs.map((run) => <li key={run.run_id}><span><strong>{run.agent_name}</strong><small>{run.run_id}</small></span><span className="run-actions"><span className="status-pill running">{run.status}</span><button className="text-button" type="button" onClick={() => loadReplay(run.run_id)}>Replay</button></span></li>)}
    </ul>
  )

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
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
              aria-current={activePanel === item.id ? 'page' : undefined}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>

        <main className="content" id="main-content" tabIndex={-1} aria-labelledby="panel-title">
          <div className="content-heading">
            <div>
              <p className="eyebrow">{user.org_public_id}</p>
              <h2 id="panel-title">{activeLabel}</h2>
            </div>
            <span className="session-badge" role="status">OIDC session active</span>
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
                {renderRunList()}
              </section>
            </div>
          ) : activePanel === 'runs' ? (
            <section className="data-panel">
              <div className="panel-title-row"><h3>Active runs</h3><span className="count-badge">{runs.length}</span></div>
              {renderRunList()}
              {replay && selectedRunId ? <section className="replay-panel" aria-labelledby="replay-title"><div className="panel-title-row"><h3 id="replay-title">Redacted session replay</h3><div className="run-actions"><button className="control-button" type="button" onClick={() => sendIntervention('pause')}>Pause</button><button className="control-button" type="button" onClick={() => sendIntervention('resume')}>Resume</button><button className="control-button danger" type="button" onClick={() => sendIntervention('terminate')}>Terminate</button><button className="control-button danger" type="button" onClick={() => sendIntervention('kill')}>Kill</button></div></div><p className="muted">Only redacted metadata is shown. Prompts, source code, credentials, and decision tokens are never rendered.</p><ul className="timeline">{replay.actions.map((action) => <li key={action.action_id}><strong>#{action.sequence} {action.action_type}</strong><span>{action.provider_profile}</span>{action.decision ? <span className="status-pill">{action.decision.verdict} · {action.decision.reason}</span> : null}</li>)}{replay.audit_events.map((event) => <li key={event.event_id}><strong>{event.event_type}</strong><span>Audit event</span></li>)}</ul></section> : null}
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
          ) : activePanel === 'policies' ? (
            <section className="data-panel">
              <div className="panel-title-row"><h3>Policy editor</h3><span className={`live-indicator ${liveStatus}`} role="status" aria-live="polite">{liveStatus} live feed · {liveEvents.length} events</span></div>
              {policies.length === 0 ? <><p className="muted">No policy set has been configured for this organization.</p><input className="policy-name" aria-label="Policy name" value={policyName} onChange={(event) => setPolicyName(event.target.value)} /><textarea className="policy-editor" aria-label="Policy rules JSON" value={policyJson} onChange={(event) => setPolicyJson(event.target.value)} /><button className="control-button" disabled={!['owner', 'admin'].includes(user.role)} type="button" onClick={createPolicySet}>Create policy draft</button></> : <><p className="muted">Editing <strong>{policies[0].name}</strong>. New changes are drafts until explicitly published.</p><textarea className="policy-editor" aria-label="Policy rules JSON" value={policyJson} onChange={(event) => setPolicyJson(event.target.value)} /><div className="run-actions"><button className="control-button" disabled={!['owner', 'admin'].includes(user.role)} type="button" onClick={updatePolicyVersion}>Save draft version</button><button className="control-button" disabled={!['owner', 'admin'].includes(user.role) || !policies[0].versions.some((version) => !version.is_published)} type="button" onClick={publishLatestPolicy}>Publish latest draft</button></div>{policyMessage ? <p className="muted" role="status">{policyMessage}</p> : null}<ul className="data-list">{policies[0].versions.map((version) => <li key={version.version_id}><span><strong>Version {version.version_number}</strong><small>{new Date(version.created_at).toLocaleString()}</small></span><span className={version.is_published ? 'status-pill enabled' : 'status-pill'}>{version.is_published ? 'published' : 'draft'}</span></li>)}</ul></>}
            </section>
          ) : activePanel === 'metrics' ? (
            <section className="data-panel">
              <div className="card-grid compact-grid"><section className="metric-card"><span className="metric-label">Virtual cost</span><strong>{metrics ? `${metrics.total_virtual_cost_micro_usd} μUSD` : '—'}</strong><p>Local governance estimate</p></section><section className="metric-card"><span className="metric-label">Provider cost</span><strong>{metrics?.total_provider_cost_micro_usd == null ? 'Unavailable' : `${metrics.total_provider_cost_micro_usd} μUSD`}</strong><p>Only provider-reported values</p></section><section className="metric-card"><span className="metric-label">Energy</span><strong>{metrics?.total_energy_wh == null ? 'Unavailable' : `${metrics.total_energy_wh.toFixed(2)} Wh`}</strong><p>Measured or estimated telemetry</p></section></div>
              <ul className="data-list">{(metrics?.providers ?? []).map((metric) => <li key={`${metric.provider_kind}-${metric.model_profile}`}><span><strong>{metric.model_profile}</strong><small>{metric.provider_kind} · {metric.actions} actions · {metric.avg_latency_ms == null ? 'latency unavailable' : `${metric.avg_latency_ms.toFixed(0)} ms avg`}</small></span><span className={`status-pill ${metric.health_status === 'healthy' ? 'enabled' : 'disabled'}`}>{metric.health_status} · quota {metric.quota_status}</span></li>)}</ul>
            </section>
          ) : activePanel === 'evaluations' ? (
            <section className="data-panel"><h3>Model evaluation comparison</h3><p className="muted">Scores are shown only when persisted evaluation signals exist; no synthetic scores are generated.</p>{evaluations.length === 0 ? <p className="muted">No model evaluation telemetry is available yet.</p> : <div className="profile-grid">{evaluations.map((model) => <article className="profile-card" key={`${model.provider_kind}-${model.model_profile}`}><div className="profile-heading"><h3>{model.model_profile}</h3><span className="status-pill">{model.evaluation_available ? 'evaluated' : 'telemetry only'}</span></div><p>{model.provider_kind} · {model.samples} samples</p><small>Latency: {model.average_latency_ms == null ? '—' : `${model.average_latency_ms.toFixed(0)} ms`} · Virtual cost: {model.average_virtual_cost_micro_usd == null ? '—' : `${model.average_virtual_cost_micro_usd.toFixed(1)} μUSD`}</small><small>EV: {model.average_ev_score == null ? '—' : model.average_ev_score.toFixed(3)} · Loop: {model.average_loop_score == null ? '—' : model.average_loop_score.toFixed(3)}</small></article>)}</div>}</section>
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
