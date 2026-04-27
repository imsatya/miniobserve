const BASE = '/api'

const STORAGE_KEY = 'miniobserve_api_key'
const SKIP_KEY = 'miniobserve_skip_key'

/** OSS local default; must match ``LOCAL_DEFAULT_API_KEY`` in ``backend/auth.py`` / AGENTS.md. */
export const LOCAL_DEFAULT_API_KEY = 'sk-local-default-key'

export function getApiKey() {
  if (typeof sessionStorage !== 'undefined') {
    const stored = sessionStorage.getItem(STORAGE_KEY)
    if (stored) return stored
  }
  return import.meta.env?.VITE_MINIOBSERVE_API_KEY || null
}

/** Mask key for display (e.g. sk-local-***-key). */
export function maskApiKey(key) {
  if (!key || key.length < 12) return key ? '••••••••' : ''
  return key.slice(0, 8) + '••••••' + key.slice(-4)
}

/** True if user has passed the login screen (has key or chose to skip for local mode). */
export function hasPassedLogin() {
  if (getApiKey()) return true
  if (typeof sessionStorage !== 'undefined' && sessionStorage.getItem(SKIP_KEY)) return true
  return false
}

export function setApiKey(key) {
  if (typeof sessionStorage !== 'undefined') {
    if (key) {
      sessionStorage.setItem(STORAGE_KEY, key)
      sessionStorage.removeItem(SKIP_KEY)
    } else {
      sessionStorage.setItem(SKIP_KEY, '1')
      sessionStorage.removeItem(STORAGE_KEY)
    }
  }
}

export function clearApiKey() {
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(STORAGE_KEY)
    sessionStorage.removeItem(SKIP_KEY)
  }
}

function authHeaders() {
  const key = getApiKey()
  if (!key) return {}
  return { Authorization: `Bearer ${key}` }
}

/**
 * Same-origin fetch with API key headers. Retries once on 401 to absorb occasional
 * dropped Authorization headers (reverse proxies) or brief races after sessionStorage updates.
 */
async function authFetch(url, init = {}) {
  const merged = {
    ...init,
    headers: { ...(init.headers || {}), ...authHeaders() },
  }
  let r = await fetch(url, merged)
  if (r.status === 401) {
    await new Promise((res) => setTimeout(res, 80))
    r = await fetch(url, merged)
  }
  return r
}

/** Throw if response is 401 so callers can show "invalid key" UI. */
async function checkAuth(r) {
  if (r.status === 401) {
    const e = new Error('UNAUTHORIZED')
    e.status = 401
    e.detail = await r.json().catch(() => ({}))
    throw e
  }
}

export async function fetchStats(appName) {
  const q = appName ? `?app_name=${appName}` : ''
  const r = await authFetch(`${BASE}/stats${q}`)
  await checkAuth(r)
  return r.json()
}

export async function fetchLogs({ limit = 50, offset = 0, model, provider, appName, hasError, search } = {}) {
  const p = new URLSearchParams()
  p.set('limit', limit)
  p.set('offset', offset)
  if (model) p.set('model', model)
  if (provider) p.set('provider', provider)
  if (appName) p.set('app_name', appName)
  // Only send when true/false. `null` (meaning "all" in UI) must omit the param — otherwise
  // URLSearchParams coerces null to the string "null" and breaks Optional[bool] on the server.
  if (typeof hasError === 'boolean') p.set('has_error', hasError)
  if (search) p.set('search', search)
  const r = await authFetch(`${BASE}/logs?${p}`)
  await checkAuth(r)
  return r.json()
}

export async function fetchLog(id) {
  const r = await authFetch(`${BASE}/logs/${id}`)
  await checkAuth(r)
  return r.json()
}

export async function clearLogs(appName) {
  const q = appName ? `?app_name=${appName}` : ''
  const r = await authFetch(`${BASE}/logs${q}`, { method: 'DELETE' })
  await checkAuth(r)
}

/** Public: which DB backend this server uses (`sqlite` | `supabase`). No API key required. */
export async function fetchBackend() {
  const r = await fetch(`${BASE}/backend`)
  if (!r.ok) {
    const e = new Error('BACKEND_FETCH')
    e.status = r.status
    throw e
  }
  return r.json()
}

export async function fetchMe() {
  const r = await authFetch(`${BASE}/me`)
  await checkAuth(r)
  return r.json()
}

export async function fetchAccessLog() {
  const r = await authFetch(`${BASE}/access-log`)
  await checkAuth(r)
  return r.json()
}

export async function fetchRuns({ scan_limit = 8000, runs_limit = 100 } = {}) {
  const p = new URLSearchParams()
  p.set('scan_limit', String(scan_limit))
  p.set('runs_limit', String(runs_limit))
  const r = await authFetch(`${BASE}/runs?${p}`)
  await checkAuth(r)
  return r.json()
}

export async function fetchRunDetail(runKey) {
  const p = new URLSearchParams({ run_key: runKey })
  const r = await authFetch(`${BASE}/run-logs?${p}`)
  await checkAuth(r)
  return r.json()
}

export async function fetchRunReplay(runKey) {
  const p = new URLSearchParams({ run_key: runKey })
  const r = await authFetch(`${BASE}/replay/run?${p}`)
  await checkAuth(r)
  return r.json()
}

/** Build-time: show “Get a trial key” on the login screen when the hosted playground is enabled. */
export function trialMintEnabled() {
  const v = import.meta.env?.VITE_MINIOBSERVE_PUBLIC_TRIAL
  return v === '1' || String(v).toLowerCase() === 'true'
}

/** Public trial mint (no auth). Server must set MINIOBSERVE_PUBLIC_TRIAL_MINT=1. */
export async function mintTrialApiKey() {
  const r = await fetch(`${BASE}/trial/api-keys`, { method: 'POST' })
  if (r.status === 429) {
    const e = new Error('RATE_LIMIT')
    e.status = 429
    throw e
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      detail = (await r.text()) || detail
    }
    const e = new Error(detail)
    e.status = r.status
    throw e
  }
  return r.json()
}

/** Operator mint; admin secret is not stored by this helper (caller passes per request). */
export async function mintAdminApiKey({ adminSecret, appName, label }) {
  const r = await fetch(`${BASE}/admin/api-keys`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${adminSecret}`,
    },
    body: JSON.stringify({
      app_name: appName,
      ...(label ? { label } : {}),
    }),
  })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      detail = (await r.text()) || detail
    }
    throw new Error(detail)
  }
  return r.json()
}
