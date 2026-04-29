import { useState, useEffect, useCallback } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar
} from 'recharts'
import {
  Activity, DollarSign, Zap, AlertTriangle, Search,
  RefreshCw, ChevronLeft, ChevronRight, Trash2, Eye, Key
} from 'lucide-react'
import {
  fetchStats, fetchLogs, fetchLog, fetchRunDetail, clearLogs, getApiKey, setApiKey, clearApiKey, hasPassedLogin,
  fetchMe, maskApiKey, fetchAccessLog, fetchBackend, LOCAL_DEFAULT_API_KEY,
  trialMintEnabled, mintTrialApiKey, mintAdminApiKey,
} from './api.js'
import StatCard from './components/StatCard.jsx'
import LogModal from './components/LogModal.jsx'
import TracePanel from './components/TracePanel.jsx'
import { runIdFromLog } from './metadata.js'
import { formatLocalTimestamp } from './formatTime.js'

const fmt = {
  cost: v => `$${(v || 0).toFixed(4)}`,
  tokens: v => (v || 0).toLocaleString(),
  ms: v => `${(v || 0).toFixed(0)}ms`,
  pct: v => `${(v || 0).toFixed(1)}%`,
}

const POLL_MS = 60_000

export default function App() {
  const [apiKeySet, setApiKeySet] = useState(() => hasPassedLogin())
  const [stats, setStats] = useState(null)
  const [logs, setLogs] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [search, setSearch] = useState('')
  const [filterErr, setFilterErr] = useState(null)
  /** Modal payload: log row + optional run steps when opened from Trace (for cache-boundary LCP). */
  const [selectedLog, setSelectedLog] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('trace') // 'trace' | 'logs' | 'summary' | 'settings'
  const [currentApp, setCurrentApp] = useState(null) // app name for current API key (from /api/me)
  const [unauthorized, setUnauthorized] = useState(false) // server returned 401
  const [probeDone, setProbeDone] = useState(false) // when no key: have we checked if server allows unauthenticated?
  const [accessEntries, setAccessEntries] = useState([]) // recent API hits (from /api/access-log)
  const [trialBusy, setTrialBusy] = useState(false)
  const [trialErr, setTrialErr] = useState(null)
  const [trialResult, setTrialResult] = useState(null)
  const [mintAdminSecret, setMintAdminSecret] = useState('')
  const [mintAppName, setMintAppName] = useState('')
  const [mintLabel, setMintLabel] = useState('')
  const [mintBusy, setMintBusy] = useState(false)
  const [mintErr, setMintErr] = useState(null)
  const [mintResult, setMintResult] = useState(null)
  /** Bumped after DELETE /api/logs so Trace → RunPanel refetches runs immediately (not only on 60s poll). */
  const [runsRefreshNonce, setRunsRefreshNonce] = useState(0)
  /** `undefined` = loading; `''` = fetch failed / unknown; else raw `MINIOBSERVE_BACKEND` value. */
  const [serverDatabase, setServerDatabase] = useState(undefined)
  const LIMIT = 50

  useEffect(() => {
    fetchBackend()
      .then((j) => setServerDatabase((j.backend || '').trim().toLowerCase()))
      .catch(() => setServerDatabase(''))
  }, [])

  const databaseDisplay =
    serverDatabase === undefined
      ? '…'
      : serverDatabase === ''
        ? 'unknown'
        : serverDatabase === 'sqlite'
          ? 'SQLite'
          : serverDatabase === 'supabase'
            ? 'Supabase'
            : serverDatabase

  // On load: if URL has ?key=..., set it and strip from URL (for /go/:app_name?key=... direct links)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const keyFromUrl = params.get('key')
    if (keyFromUrl) {
      setApiKey(keyFromUrl)
      setApiKeySet(true)
      setProbeDone(true)
      params.delete('key')
      const newSearch = params.toString()
      const newUrl = window.location.pathname + (newSearch ? `?${newSearch}` : '') + window.location.hash
      window.history.replaceState({}, '', newUrl)
    }
  }, [])

  // When we have no key but user previously "continued" (skip): probe server. If 401, show login.
  useEffect(() => {
    if (getApiKey() || probeDone) return
    if (!apiKeySet) return
    let cancelled = false
    fetch('/api/me', { headers: {} })
      .then((r) => {
        if (cancelled) return
        if (r.status === 401) {
          clearApiKey()
          setApiKeySet(false)
        }
        setProbeDone(true)
      })
      .catch(() => setProbeDone(true))
    return () => { cancelled = true }
  }, [apiKeySet])

  // Resolve current app name for the active API key
  useEffect(() => {
    if (!getApiKey()) {
      setCurrentApp('default')
      return
    }
    setProbeDone(true)
    fetchMe()
      .then((d) => {
        setCurrentApp(d.app_name || null)
        setUnauthorized(false)
      })
      .catch((err) => { setCurrentApp(null); if (err.message === 'UNAUTHORIZED') setUnauthorized(true) })
  }, [apiKeySet])

  // Hooks must run unconditionally: keep load + polling above any early return (login / probe UI).
  const load = useCallback(async () => {
    setLoading(true)
    setUnauthorized(false)
    try {
      const [s, l, a] = await Promise.all([
        fetchStats(),
        fetchLogs({ limit: LIMIT, offset: page * LIMIT, search, hasError: filterErr }),
        fetchAccessLog(),
      ])
      setStats(s)
      setLogs(l.logs || [])
      setTotal(l.total || 0)
      setAccessEntries(a.entries || [])
    } catch (err) {
      if (err.message === 'UNAUTHORIZED') setUnauthorized(true)
    }
    setLoading(false)
  }, [page, search, filterErr])

  useEffect(() => {
    if (!apiKeySet) return
    if (!getApiKey() && !probeDone) return
    load()
  }, [load, apiKeySet, probeDone])

  useEffect(() => {
    if (!apiKeySet) return
    if (!getApiKey() && !probeDone) return
    const poll = () => {
      fetchStats()
        .then((s) => {
          setStats(s)
          setUnauthorized(false)
        })
        .catch((err) => { if (err.message === 'UNAUTHORIZED') setUnauthorized(true) })
      fetchAccessLog().then((a) => setAccessEntries(a.entries || [])).catch(() => {})
      fetchLogs({ limit: LIMIT, offset: page * LIMIT, search, hasError: filterErr })
        .then((l) => {
          setLogs(l.logs || [])
          setTotal(l.total || 0)
          setUnauthorized(false)
        })
        .catch((err) => { if (err.message === 'UNAUTHORIZED') setUnauthorized(true) })
    }
    const t = setInterval(poll, POLL_MS)
    return () => clearInterval(t)
  }, [page, search, filterErr, apiKeySet, probeDone])

  // When no API key and server may require one, show login screen (key stored in sessionStorage)
  if (!apiKeySet) {
    return (
      <div className="min-h-screen bg-page text-ink flex items-center justify-center p-6">
        <div className="w-full max-w-sm bg-surface border border-line rounded-xl p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-lg bg-[#7c6af7]/20 border border-[#7c6af7]/30 flex items-center justify-center">
              <Key size={20} className="text-ink" />
            </div>
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="font-mono font-semibold">miniobserve</span>
              <span
                className="text-muted font-mono text-[10px] uppercase tracking-widest truncate"
                title={serverDatabase && serverDatabase !== '' ? `MINIOBSERVE_BACKEND=${serverDatabase}` : 'Database backend for this server'}
              >
                DB: {databaseDisplay}
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <button
              type="button"
              className="w-full py-2.5 rounded-lg bg-[#7c6af7] text-white border border-[#7c6af7] font-mono text-sm hover:bg-[#6b5ce6] transition-colors shadow-sm"
              onClick={() => {
                setApiKey(LOCAL_DEFAULT_API_KEY)
                setApiKeySet(true)
              }}
            >
              Default Local Login
            </button>
            <p className="text-center text-[10px] font-mono uppercase tracking-widest text-muted">or</p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const key = (e.target.elements?.key?.value || '').trim()
              setApiKey(key || null)
              setApiKeySet(true)
            }}
            className="flex flex-col gap-3 mt-1"
          >
            <input
              name="key"
              type="password"
              autoComplete="off"
              placeholder="Custom API key (optional: leave empty only if server allows no header)"
              className="w-full bg-inset border border-line rounded-lg px-3 py-2.5 text-sm font-mono text-ink placeholder-muted focus:outline-none focus:border-[#7c6af7]/50"
            />
            <button
              type="submit"
              className="w-full py-2.5 rounded-lg bg-inset text-ink border border-line font-mono text-sm hover:bg-inset/80 transition-colors"
            >
              Continue
            </button>
          </form>
          {trialMintEnabled() && (
            <div className="border-t border-line mt-5 pt-5">
              <p className="text-muted text-xs mb-3">
                Get a new isolated app with its own API key.
              </p>
              {trialResult ? (
                <div className="flex flex-col gap-2">
                  <div className="text-xs font-mono text-[#22d3a0] break-all bg-inset border border-line rounded-lg p-2">
                    {trialResult.api_key}
                  </div>
                  <p className="text-muted text-xs font-mono">app: {trialResult.app_name}</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg bg-inset text-ink border border-line font-mono text-xs"
                      onClick={() => {
                        setApiKey(trialResult.api_key)
                        setTrialResult(null)
                        setTrialErr(null)
                        setApiKeySet(true)
                      }}
                    >
                      Use this key
                    </button>
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg border border-line text-muted font-mono text-xs hover:border-lineSoft"
                      onClick={() => {
                        if (trialResult?.api_key) navigator.clipboard?.writeText(trialResult.api_key)
                      }}
                    >
                      Copy key
                    </button>
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg border border-line text-muted font-mono text-xs hover:border-lineSoft"
                      onClick={() => { setTrialResult(null); setTrialErr(null) }}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  disabled={trialBusy}
                  onClick={async () => {
                    setTrialErr(null)
                    setTrialBusy(true)
                    try {
                      const data = await mintTrialApiKey()
                      setTrialResult(data)
                    } catch (e) {
                      setTrialErr(e.message === 'RATE_LIMIT' ? 'Too many requests from this network; try again later.' : (e.message || 'Request failed'))
                    }
                    setTrialBusy(false)
                  }}
                  className="w-full py-2.5 rounded-lg border border-line text-muted font-mono text-sm hover:border-[#7c6af7]/40 disabled:opacity-50"
                >
                  {trialBusy ? 'Creating…' : 'Get an API key'}
                </button>
              )}
              {trialErr && <p className="text-amber-600 text-xs font-mono mt-2">{trialErr}</p>}
            </div>
          )}
        </div>
      </div>
    )
  }

  // No key but user skipped login: wait for probe. If server requires key we already cleared and show login above.
  if (apiKeySet && !getApiKey() && !probeDone) {
    return (
      <div className="min-h-screen bg-page text-ink flex items-center justify-center">
        <div className="text-muted font-mono text-sm">Checking server…</div>
      </div>
    )
  }

  const openLog = async (id, { runKey, steps, siblings, returnTo, modalMode, decisionAggregate } = {}) => {
    try {
      const log = await fetchLog(id)
      let runSteps = steps
      if (runKey && runSteps == null) {
        const d = await fetchRunDetail(runKey)
        runSteps = d.steps || []
      }
      setSelectedLog({
        log,
        runSteps: runSteps || null,
        runKey: runKey || null,
        siblings: siblings || null,
        returnTo: returnTo || null,
        modalMode: modalMode || null,
        decisionAggregate: decisionAggregate || null,
      })
    } catch (err) {
      if (err.message === 'UNAUTHORIZED') setUnauthorized(true)
    }
  }

  const handleClear = async () => {
    if (!confirm('Clear all logs?')) return
    try {
      await clearLogs()
      setRunsRefreshNonce((n) => n + 1)
      load()
    } catch (err) {
      if (err.message === 'UNAUTHORIZED') setUnauthorized(true)
    }
  }

  const StatusDot = ({ error }) => (
    <span className={`inline-block w-1.5 h-1.5 rounded-full ${error ? 'bg-[#f75f6a]' : 'bg-[#22d3a0]'}`} />
  )

  const lastSeenTraffic =
    accessEntries.length > 0 && accessEntries[0]?.timestamp
      ? new Date(accessEntries[0].timestamp).toLocaleString(undefined, {
          dateStyle: 'medium',
          timeStyle: 'medium',
        })
      : null

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    return (
      <div className="bg-surface border border-line shadow-md rounded-lg p-3 text-xs font-mono text-ink">
        <div className="text-muted mb-1">{label}</div>
        {payload.map(p => (
          <div key={p.dataKey} style={{ color: p.color }}>{p.name}: {p.value}</div>
        ))}
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-page text-ink">
      {/* Header */}
      <header className="border-b border-line px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[#7c6af7]/20 border border-[#7c6af7]/30 flex items-center justify-center">
            <Activity size={14} className="text-ink" />
          </div>
          <span className="font-mono font-semibold text-sm tracking-tight">miniobserve</span>
          <span className="text-muted font-mono text-xs">v0.1.0</span>
          <span
            className="text-muted font-mono text-[10px] uppercase tracking-widest border border-line rounded px-2 py-0.5 shrink-0"
            title={serverDatabase && serverDatabase !== '' ? `MINIOBSERVE_BACKEND=${serverDatabase}` : 'Database backend'}
          >
            DB: {databaseDisplay}
          </span>
          {getApiKey() && (
            <span className="text-muted font-mono text-xs border-l border-line pl-3" title="Current API key">
              Key: {maskApiKey(getApiKey())}
            </span>
          )}
          {currentApp != null && (
            <span className="text-muted font-mono text-xs" title="App for this key">
              App: <span className="text-ink">{currentApp}</span>
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {['trace', 'logs', 'summary', 'settings'].map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-1.5 rounded-lg text-xs font-mono transition-colors ${
                tab === t
                  ? 'bg-[#7c6af7]/15 text-ink border border-line'
                  : 'text-muted hover:text-ink'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          {getApiKey() && (
            <button
              onClick={() => { clearApiKey(); setApiKeySet(false); setUnauthorized(false) }}
              className="px-2 py-1 text-muted hover:text-ink text-xs font-mono"
            >
              Log out
            </button>
          )}
          <button
            onClick={load}
            disabled={loading}
            className="p-2 text-muted hover:text-ink transition-colors"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={handleClear}
            className="p-2 text-muted hover:text-[#f75f6a] transition-colors"
          >
            <Trash2 size={14} />
          </button>
          {/* Live dot */}
          <div className="flex items-center gap-1.5 text-[#22d3a0] text-xs font-mono">
            <span className="w-1.5 h-1.5 bg-[#22d3a0] rounded-full animate-pulse" />
            live
          </div>
        </div>
      </header>

      {unauthorized && (
        <div className="bg-[#f75f6a]/15 border-b border-[#f75f6a]/30 px-6 py-3 flex items-center justify-between gap-4">
          <span className="text-sm text-[#f75f6a]">
            401 Unauthorized — Invalid or missing API key. Log out and enter the key for this server.
          </span>
          <button
            onClick={() => { clearApiKey(); setApiKeySet(false); setUnauthorized(false) }}
            className="px-3 py-1.5 rounded bg-[#f75f6a]/20 text-[#f75f6a] text-xs font-mono hover:bg-[#f75f6a]/30"
          >
            Log out & enter key
          </button>
        </div>
      )}

      <main className="px-6 py-6 max-w-7xl mx-auto">
        {/* SUMMARY TAB */}
        {tab === 'summary' && stats && (
          <div className="flex flex-col gap-6">
            <div className="bg-surface border border-line rounded-xl p-5">
              <p className="text-sm font-mono text-ink">
                <span className="text-muted">Last Seen Traffic:</span>{' '}
                {lastSeenTraffic ?? <span className="text-muted">No requests yet</span>}
              </p>
            </div>

            {/* Stat cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard label="Total Calls" value={stats.total_calls?.toLocaleString()} icon={Activity} color="accent" />
              <StatCard label="Total Cost" value={fmt.cost(stats.total_cost_usd)} icon={DollarSign} color="green" sub={`${fmt.tokens(stats.total_tokens)} tokens`} />
              <StatCard label="Avg Latency" value={fmt.ms(stats.avg_latency_ms)} icon={Zap} color="yellow" />
              <StatCard label="Error Rate" value={fmt.pct(stats.error_rate_pct)} icon={AlertTriangle} color={stats.error_rate_pct > 5 ? 'red' : 'green'} sub={`${stats.error_count} errors`} />
            </div>

            {/* Charts row */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Daily calls chart */}
              <div className="bg-surface border border-line rounded-xl p-5">
                <div className="text-xs font-mono text-muted uppercase tracking-widest mb-4">Calls / Day</div>
                <ResponsiveContainer width="100%" height={160}>
                  <AreaChart data={stats.daily || []}>
                    <defs>
                      <linearGradient id="gCalls" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#7c6af7" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#7c6af7" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="day" tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                    <YAxis tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                    <Tooltip content={<CustomTooltip />} />
                    <Area type="monotone" dataKey="calls" name="calls" stroke="#7c6af7" fill="url(#gCalls)" strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Daily cost chart */}
              <div className="bg-surface border border-line rounded-xl p-5">
                <div className="text-xs font-mono text-muted uppercase tracking-widest mb-4">Cost / Day ($)</div>
                <ResponsiveContainer width="100%" height={160}>
                  <AreaChart data={stats.daily || []}>
                    <defs>
                      <linearGradient id="gCost" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3a0" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#22d3a0" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="day" tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                    <YAxis tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }} />
                    <Tooltip content={<CustomTooltip />} />
                    <Area type="monotone" dataKey="cost" name="cost $" stroke="#22d3a0" fill="url(#gCost)" strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Models breakdown */}
            {stats.models?.length > 0 && (
              <div className="bg-surface border border-line rounded-xl p-5">
                <div className="text-xs font-mono text-muted uppercase tracking-widest mb-4">Models</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs font-mono">
                    <thead>
                      <tr className="text-muted border-b border-line">
                        {['Provider', 'Model', 'Calls', 'Tokens', 'Cost', 'Avg Latency'].map(h => (
                          <th key={h} className="text-left pb-2 pr-4 font-normal">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {stats.models.map((m, i) => (
                        <tr key={i} className="border-b border-line/50 hover:bg-inset">
                          <td className="py-2 pr-4 text-ink">{m.provider}</td>
                          <td className="py-2 pr-4 text-ink">{m.model}</td>
                          <td className="py-2 pr-4 text-ink">{m.calls?.toLocaleString()}</td>
                          <td className="py-2 pr-4 text-ink">{m.tokens?.toLocaleString()}</td>
                          <td className="py-2 pr-4 text-[#22d3a0]">${m.cost?.toFixed(4)}</td>
                          <td className="py-2 pr-4 text-[#f7c948]">{m.avg_latency?.toFixed(0)}ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* SETTINGS TAB */}
        {tab === 'settings' && (
          <div className="flex flex-col gap-6">
            <div className="bg-surface border border-line rounded-xl p-5">
              <div className="text-xs font-mono text-muted uppercase tracking-widest mb-3">Mint API key</div>
              <p className="text-muted text-xs mb-4">
                Create a new data API key for an app tenant. Requires server{' '}
                <span className="text-ink">MINIOBSERVE_ADMIN_SECRET</span> and{' '}
                <span className="text-ink">MINIOBSERVE_API_KEY_PEPPER</span>. The key is shown only once.
              </p>
              {mintResult ? (
                <div className="flex flex-col gap-2 mb-4">
                  <div className="text-xs font-mono text-[#22d3a0] break-all bg-inset border border-line rounded-lg p-2">
                    {mintResult.api_key}
                  </div>
                  <p className="text-muted text-xs font-mono">app: {mintResult.app_name}</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg bg-inset text-ink border border-line font-mono text-xs"
                      onClick={async () => {
                        setApiKey(mintResult.api_key)
                        setMintResult(null)
                        setMintErr(null)
                        try {
                          const d = await fetchMe()
                          setCurrentApp(d.app_name || null)
                          setUnauthorized(false)
                        } catch (err) {
                          if (err.message === 'UNAUTHORIZED') setUnauthorized(true)
                        }
                      }}
                    >
                      Switch dashboard to this key
                    </button>
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg border border-line text-muted font-mono text-xs"
                      onClick={() => { if (mintResult?.api_key) navigator.clipboard?.writeText(mintResult.api_key) }}
                    >
                      Copy
                    </button>
                    <button
                      type="button"
                      className="px-3 py-2 rounded-lg border border-line text-muted font-mono text-xs"
                      onClick={() => setMintResult(null)}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-3 mb-4">
                  <input
                    type="password"
                    autoComplete="off"
                    placeholder="Admin secret (Bearer value)"
                    value={mintAdminSecret}
                    onChange={(e) => setMintAdminSecret(e.target.value)}
                    className="w-full bg-inset border border-line rounded-lg px-3 py-2.5 text-sm font-mono text-ink placeholder-muted focus:outline-none focus:border-[#7c6af7]/50"
                  />
                  <input
                    type="text"
                    autoComplete="off"
                    placeholder="app_name (e.g. my-app)"
                    value={mintAppName}
                    onChange={(e) => setMintAppName(e.target.value)}
                    className="w-full bg-inset border border-line rounded-lg px-3 py-2.5 text-sm font-mono text-ink placeholder-muted focus:outline-none focus:border-[#7c6af7]/50"
                  />
                  <input
                    type="text"
                    autoComplete="off"
                    placeholder="Label (optional)"
                    value={mintLabel}
                    onChange={(e) => setMintLabel(e.target.value)}
                    className="w-full bg-inset border border-line rounded-lg px-3 py-2.5 text-sm font-mono text-ink placeholder-muted focus:outline-none focus:border-[#7c6af7]/50"
                  />
                  <button
                    type="button"
                    disabled={mintBusy || !mintAdminSecret.trim() || !mintAppName.trim()}
                    onClick={async () => {
                      setMintErr(null)
                      setMintBusy(true)
                      try {
                        const data = await mintAdminApiKey({
                          adminSecret: mintAdminSecret.trim(),
                          appName: mintAppName.trim(),
                          label: mintLabel.trim() || undefined,
                        })
                        setMintResult(data)
                        setMintAdminSecret('')
                      } catch (e) {
                        setMintErr(e.message || 'Mint failed')
                      }
                      setMintBusy(false)
                    }}
                    className="w-full py-2.5 rounded-lg bg-inset text-ink border border-line font-mono text-sm disabled:opacity-40"
                  >
                    {mintBusy ? 'Creating…' : 'Generate API key'}
                  </button>
                </div>
              )}
              {mintErr && <p className="text-amber-600 text-xs font-mono">{mintErr}</p>}
            </div>
            <div className="bg-surface border border-line rounded-xl p-5">
              <div className="text-xs font-mono text-muted uppercase tracking-widest mb-3">API traffic (this server)</div>
              <p className="text-muted text-xs mb-3">Incoming requests to <span className="text-ink">/api/*</span>, newest first.</p>
              <div className="max-h-48 overflow-y-auto rounded-lg border border-line bg-inset">
                {accessEntries.length === 0 ? (
                  <div className="p-4 text-xs text-muted font-mono">No requests yet.</div>
                ) : (
                  <table className="w-full text-xs font-mono">
                    <thead className="sticky top-0 bg-surface border-b border-line">
                      <tr className="text-muted">
                        <th className="text-left px-3 py-2 font-normal">Method</th>
                        <th className="text-left px-3 py-2 font-normal">URL</th>
                        <th className="text-left px-3 py-2 font-normal whitespace-nowrap">Time (local)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {accessEntries.map((row, i) => (
                        <tr key={`${row.timestamp}-${i}`} className="border-b border-line/40 hover:bg-inset">
                          <td className="px-3 py-1.5 text-ink whitespace-nowrap">{row.method}</td>
                          <td className="px-3 py-1.5 text-ink break-all">{row.url}</td>
                          <td className="px-3 py-1.5 text-muted whitespace-nowrap">
                            {row.timestamp
                              ? new Date(row.timestamp).toLocaleString(undefined, {
                                  dateStyle: 'short',
                                  timeStyle: 'medium',
                                })
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        )}

        {/* TRACE TAB */}
        {tab === 'trace' && (
          <TracePanel onOpenLog={openLog} runsRefreshNonce={runsRefreshNonce} />
        )}

        {/* LOGS TAB */}
        {tab === 'logs' && (
          <div className="flex flex-col gap-4">
            {/* Filters */}
            <div className="flex items-center gap-3">
              <div className="relative flex-1 max-w-sm">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  value={search}
                  onChange={e => { setSearch(e.target.value); setPage(0) }}
                  placeholder="Search prompts, responses..."
                  className="w-full bg-surface border border-line rounded-lg pl-8 pr-3 py-2 text-xs font-mono text-ink placeholder-muted focus:outline-none focus:border-[#7c6af7]/50"
                />
              </div>
              {[
                { label: 'all', value: null },
                { label: 'errors only', value: true },
                { label: 'ok only', value: false },
              ].map(f => (
                <button
                  key={f.label}
                  onClick={() => { setFilterErr(f.value); setPage(0) }}
                  className={`px-3 py-2 rounded-lg text-xs font-mono border transition-colors ${
                    filterErr === f.value
                      ? 'bg-[#7c6af7]/15 text-ink border-line'
                      : 'text-muted border-line hover:border-lineSoft'
                  }`}
                >
                  {f.label}
                </button>
              ))}
              <span className="text-muted font-mono text-xs ml-auto">{total.toLocaleString()} total</span>
            </div>

            {/* Logs table */}
            <div className="bg-surface border border-line rounded-xl overflow-hidden">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="text-muted border-b border-line">
                    {['', 'Model', 'Provider', 'Run', 'Tokens', 'Cost', 'Latency', 'Time (local)', ''].map((h, i) => (
                      <th key={i} className="text-left px-4 py-3 font-normal">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.length === 0 && (
                    <tr>
                      <td colSpan={9} className="px-4 py-12 text-center text-muted">
                        No logs yet. Start making LLM calls with the SDK.
                      </td>
                    </tr>
                  )}
                  {logs.map(log => (
                    <tr
                      key={log.id}
                      className="border-b border-line/50 hover:bg-inset cursor-pointer transition-colors"
                      onClick={() => openLog(log.id, { siblings: logs.map(l => l.id) })}
                    >
                      <td className="px-4 py-3"><StatusDot error={log.error} /></td>
                      <td className="px-4 py-3 text-ink">{log.model}</td>
                      <td className="px-4 py-3 text-ink">{log.provider}</td>
                      <td className="px-4 py-3 text-muted max-w-[140px] truncate font-mono text-[10px]" title={runIdFromLog(log) || undefined}>
                        {runIdFromLog(log) || '—'}
                      </td>
                      <td className="px-4 py-3 text-muted">{log.total_tokens?.toLocaleString()}</td>
                      <td className="px-4 py-3 text-[#22d3a0]">${log.cost_usd?.toFixed(6)}</td>
                      <td className="px-4 py-3 text-[#f7c948]">{log.latency_ms?.toFixed(0)}ms</td>
                      <td className="px-4 py-3 text-muted whitespace-nowrap" title={log.timestamp || undefined}>
                        {formatLocalTimestamp(log.timestamp)}
                      </td>
                      <td className="px-4 py-3">
                        <Eye size={12} className="text-muted hover:text-ink" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {total > LIMIT && (
              <div className="flex items-center justify-between text-xs font-mono text-muted">
                <span>Page {page + 1} of {Math.ceil(total / LIMIT)}</span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(p => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="p-1 hover:text-ink disabled:opacity-30"
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <button
                    onClick={() => setPage(p => p + 1)}
                    disabled={(page + 1) * LIMIT >= total}
                    className="p-1 hover:text-ink disabled:opacity-30"
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </main>

      {selectedLog && (() => {
        // Build ordered sibling id list: run steps take priority (Trace tab), else Logs tab page.
        const siblingIds = selectedLog.siblings?.length
          ? selectedLog.siblings
          : (selectedLog.runSteps?.length ? selectedLog.runSteps.map(s => s.id) : null)
        const normalizedSiblingIds = siblingIds ? siblingIds.map((id) => String(id)) : null
        const curIdx = normalizedSiblingIds ? normalizedSiblingIds.indexOf(String(selectedLog.log.id)) : -1
        const prevId = curIdx > 0 ? siblingIds[curIdx - 1] : null
        const nextId = curIdx >= 0 && curIdx < siblingIds.length - 1 ? siblingIds[curIdx + 1] : null
        const navOpts = { runKey: selectedLog.runKey, steps: selectedLog.runSteps, siblings: selectedLog.siblings }
        const disableNav = selectedLog.modalMode === 'decision-aggregate'
        return (
          <LogModal
            log={selectedLog.log}
            modalMode={selectedLog.modalMode}
            decisionAggregate={selectedLog.decisionAggregate}
            runContext={
              selectedLog.runSteps?.length
                ? { steps: selectedLog.runSteps, runKey: selectedLog.runKey, siblings: selectedLog.siblings }
                : null
            }
            onClose={() => { const ret = selectedLog.returnTo; setSelectedLog(null); ret?.() }}
            onPrev={!disableNav && prevId != null ? () => openLog(prevId, navOpts) : null}
            onNext={!disableNav && nextId != null ? () => openLog(nextId, navOpts) : null}
          />
        )
      })()}
    </div>
  )
}
