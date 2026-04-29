/**
 * Cognitive phase colors — bright, saturated family (same vibe as the original dashboard).
 * Phases are behavioral (framework-agnostic):
 *   thinking   — LLM reasoning, no tool calls emitted yet   (violet)
 *   calling    — LLM dispatching tool calls                  (orange)
 *   synthesizing   — LLM synthesis after tools ran              (teal/green)
 *   executing  — tool or child-agent execution              (gold)
 *   unclassified — fell through all heuristics              (slate)
 * `stuck` / `waiting` are anomaly overlays (not phases).
 * Use via {@link cognitiveModeColor} for strips, dots, chips.
 */
export const COGNITIVE_MODE_COLORS = {
  thinking: '#7c6af7',
  calling: '#e07318',
  synthesizing: '#1ab896',
  executing: '#e6b435',
  unclassified: '#64748b',
  waiting: '#f59e0b',
  stuck: '#818cf8',
}

/** Canonical phase order for strips and breakdown text. */
export const COGNITIVE_MODE_ORDER = ['thinking', 'calling', 'synthesizing', 'executing', 'unclassified']

function _hexToRgb(hex) {
  const h = String(hex || '').replace('#', '').trim()
  if (h.length === 6) {
    const n = parseInt(h, 16)
    if (!Number.isFinite(n)) return { r: 71, g: 85, b: 105 }
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
  }
  if (h.length === 3) {
    const r = parseInt(h[0] + h[0], 16)
    const g = parseInt(h[1] + h[1], 16)
    const b = parseInt(h[2] + h[2], 16)
    if ([r, g, b].every((x) => Number.isFinite(x))) return { r, g, b }
  }
  return { r: 71, g: 85, b: 105 }
}

export function cognitiveModeColor(mode) {
  const key = String(mode || '').trim().toLowerCase()
  if (!key) return COGNITIVE_MODE_COLORS.unclassified
  return COGNITIVE_MODE_COLORS[key] || COGNITIVE_MODE_COLORS.unclassified
}

/** Inline styles for phase pills (header chips, legends). */
export function cognitiveModeChipStyle(mode) {
  const c = cognitiveModeColor(mode)
  const { r, g, b } = _hexToRgb(c)
  return {
    backgroundColor: `rgba(${r},${g},${b},0.14)`,
    border: `1px solid rgba(${r},${g},${b},0.42)`,
    color: c,
  }
}

/** Run list strip: agent purple, LLM green, tool yellow — matches `spanTypeAccent`. */
export const CALL_KIND_STRIP_COLORS = {
  agent: '#7c6af7',
  llm: '#22d3a0',
  tool: '#f7c948',
  other: '#64748b',
}

export function callKindStripColor(kind) {
  const k = String(kind || '').toLowerCase()
  return CALL_KIND_STRIP_COLORS[k] || CALL_KIND_STRIP_COLORS.other
}

/**
 * Build strip segments from aggregated mode fractions (fallback when per-span fingerprint_segments missing).
 */
export function fingerprintSegmentsFromModeFractions(modeFractions) {
  if (!modeFractions || typeof modeFractions !== 'object') return null
  const order = COGNITIVE_MODE_ORDER
  const out = []
  for (const k of order) {
    const v = modeFractions[k]
    if (v != null && Number(v) > 0) {
      out.push({ mode: k, fraction: Number(v) })
    }
  }
  return out.length ? out : null
}

export function formatModeBreakdown(modeFractions) {
  if (!modeFractions || typeof modeFractions !== 'object') return ''
  const order = COGNITIVE_MODE_ORDER
  const parts = []
  for (const k of order) {
    const v = modeFractions[k]
    if (v != null && Number(v) > 0) {
      parts.push(`${Math.round(Number(v) * 100)}% ${k}`)
    }
  }
  return parts.join(', ')
}

/** Dot / bar color: anomaly flags first, then cognitive phase. */
export function stepCognitiveDotColor(log) {
  if (!!log.cognitive_stuck) return COGNITIVE_MODE_COLORS.stuck
  if (!!log.cognitive_waiting) return COGNITIVE_MODE_COLORS.waiting
  if (log.cognitive_mode) return cognitiveModeColor(log.cognitive_mode)
  return stepDotColor(log)
}

export function stepTitle(log) {
  const s = (log.span_name || '').trim()
  if (s) return s
  return `${log.provider || '?'} · ${log.model || '?'}`
}

/** Normalize `log.metadata` when API returns an object or JSON string. */
export function metadataObject(log) {
  const m = log?.metadata
  if (m && typeof m === 'object' && !Array.isArray(m)) return m
  if (typeof m === 'string' && m.trim().startsWith('{')) {
    try {
      const o = JSON.parse(m)
      return o && typeof o === 'object' && !Array.isArray(o) ? o : {}
    } catch {
      return {}
    }
  }
  return {}
}

const DECISION_ALLOWED_PREFIXES = new Set(['tool', 'route', 'agent', 'workflow'])

export function normalizeDecisionId(raw) {
  const s = String(raw ?? '').trim().toLowerCase()
  if (!s) return ''
  if (s.includes(':')) {
    const i = s.indexOf(':')
    const p = s.slice(0, i).trim()
    const rest = s.slice(i + 1).trim()
    if (DECISION_ALLOWED_PREFIXES.has(p) && rest) return `${p}:${rest}`
    return s
  }
  return `workflow:${s}`
}

function coerceDecisionIds(raw) {
  const vals = Array.isArray(raw) ? raw : [raw]
  const out = []
  const seen = new Set()
  for (const v of vals) {
    const n = normalizeDecisionId(v)
    if (!n || seen.has(n)) continue
    seen.add(n)
    out.push(n)
  }
  return out
}

export function decisionBlockFromMetadata(log) {
  const md = metadataObject(log)
  const d = md.decision
  if (!d || typeof d !== 'object' || Array.isArray(d)) return null
  const out = {
    type: String(d.type || '').trim(),
    chosen: coerceDecisionIds(d.chosen),
    available: coerceDecisionIds(d.available),
    expected_downstream: coerceDecisionIds(d.expected_downstream),
    selection_signals:
      d.selection_signals && typeof d.selection_signals === 'object' && !Array.isArray(d.selection_signals)
        ? d.selection_signals
        : {},
    impact:
      d.impact && typeof d.impact === 'object' && !Array.isArray(d.impact)
        ? d.impact
        : {},
  }
  if (!out.type && !out.chosen.length && !out.available.length && !out.expected_downstream.length) return null
  return out
}

function observedDecisionIdentifiers(step) {
  const md = metadataObject(step)
  const ids = new Set()
  const canonical = new Set()
  const fallback = new Set()
  if (md.tool_name != null && String(md.tool_name).trim()) {
    const id = normalizeDecisionId(`tool:${md.tool_name}`)
    ids.add(id); canonical.add(id)
  }
  if (md.workflow_node != null && String(md.workflow_node).trim()) {
    const id = normalizeDecisionId(md.workflow_node)
    ids.add(id); canonical.add(id)
  }
  if (md.route_id != null && String(md.route_id).trim()) {
    const id = normalizeDecisionId(`route:${md.route_id}`)
    ids.add(id); canonical.add(id)
  }
  if (md.agent_name != null && String(md.agent_name).trim()) {
    const id = normalizeDecisionId(`agent:${md.agent_name}`)
    ids.add(id); fallback.add(id)
  }
  if (md.trace_lane != null && String(md.trace_lane).trim()) {
    const id = normalizeDecisionId(`route:${md.trace_lane}`)
    ids.add(id); fallback.add(id)
  }
  if (step.span_name != null && String(step.span_name).trim()) {
    const id = normalizeDecisionId(`workflow:${step.span_name}`)
    ids.add(id); fallback.add(id)
  }
  return { ids, canonical, fallback }
}

export function decisionObservabilityForStep(log, steps) {
  const decision = decisionBlockFromMetadata(log)
  if (!decision) return null
  const list = Array.isArray(steps) ? steps : []
  const byId = new Map(list.map((s) => [String(s.id), s]))
  const children = new Map()
  for (const s of list) {
    if (s?.parent_span_id == null) continue
    const pid = String(s.parent_span_id)
    if (!children.has(pid)) children.set(pid, [])
    children.get(pid).push(String(s.id))
  }
  const start = String(log.id)
  const q = [...(children.get(start) || [])]
  const seen = new Set()
  const descendants = []
  while (q.length) {
    const cur = q.shift()
    if (seen.has(cur)) continue
    seen.add(cur)
    const row = byId.get(cur)
    if (!row) continue
    descendants.push(row)
    for (const nxt of children.get(cur) || []) {
      if (!seen.has(nxt)) q.push(nxt)
    }
  }
  const observed = new Set()
  const observedCanonical = new Set()
  const observedFallback = new Set()
  for (const d of descendants) {
    const o = observedDecisionIdentifiers(d)
    for (const id of o.ids) observed.add(id)
    for (const id of o.canonical) observedCanonical.add(id)
    for (const id of o.fallback) observedFallback.add(id)
  }
  const skipped = (decision.available || []).filter((x) => !(decision.chosen || []).includes(x))
  const missing = (decision.expected_downstream || []).filter((x) => !observed.has(x))
  const usedModes = new Set()
  for (const x of decision.expected_downstream || []) {
    if (observedCanonical.has(x)) usedModes.add('canonical')
    else if (observedFallback.has(x)) usedModes.add('fallback')
  }
  const matching_mode = usedModes.size > 1 ? 'mixed' : (usedModes.size === 1 ? [...usedModes][0] : (observedFallback.size ? 'fallback' : 'canonical'))
  const computedImpact = {
    descendant_span_count: descendants.length,
    latency_ms: descendants.reduce((a, s) => a + (Number(s.latency_ms) || 0), 0),
    cost_usd: descendants.reduce((a, s) => a + (Number(s.cost_usd) || 0), 0),
    input_tokens: descendants.reduce((a, s) => a + (Number(s.input_tokens) || 0), 0),
    output_tokens: descendants.reduce((a, s) => a + (Number(s.output_tokens) || 0), 0),
    error_count: descendants.reduce((a, s) => a + (s.error ? 1 : 0), 0),
  }
  return {
    ...decision,
    skipped,
    missing_expected: missing,
    observed_identifiers: [...observed].sort(),
    matching_mode,
    impact: { reported: decision.impact || {}, computed: computedImpact },
  }
}

export function aggregateDecisionForRun(steps) {
  const list = Array.isArray(steps) ? steps : []
  const decisionSteps = list.filter((s) => decisionBlockFromMetadata(s))
  if (!decisionSteps.length) return null

  const displayDecisionId = (x) => String(x || '').replace(/^route:route:/, 'route:')
  const firstTs = decisionSteps
    .map((s) => parseStepTimestampMs(s.timestamp))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b)
  const firstTimestamp = firstTs.length ? new Date(firstTs[0]).toISOString() : null
  const lastTimestamp = firstTs.length ? new Date(firstTs[firstTs.length - 1]).toISOString() : null

  const types = new Set()
  const emitters = new Set()
  const chosen = new Set()
  const skipped = new Set()
  const expectedDownstream = new Set()
  const chronology = []
  const runStartCandidates = []

  for (const s of list) {
    const md = metadataObject(s)
    const startedAt = Date.parse(String(md.started_at || ''))
    if (Number.isFinite(startedAt)) runStartCandidates.push(startedAt)
    const endAt = parseStepTimestampMs(s.timestamp)
    const lat = Number(s.latency_ms || 0)
    if (Number.isFinite(endAt) && Number.isFinite(lat)) {
      runStartCandidates.push(Math.max(0, Math.round(endAt - lat)))
    }
  }
  const runStartMs = runStartCandidates.length ? Math.min(...runStartCandidates) : null

  for (const s of decisionSteps) {
    const md = metadataObject(s)
    const d = decisionBlockFromMetadata(s)
    if (!d) continue
    if (d.type) types.add(d.type)
    // Emitter = source node/span that emitted the decision event, not the chosen route target.
    const emitter = String(md.agent_name || md.agent_span_name || s.span_name || 'unknown').trim()
    emitters.add(displayDecisionId(emitter))
    const chosenList = (d.chosen || []).map(displayDecisionId)
    for (const x of chosenList) chosen.add(x)
    for (const x of (d.available || []).filter((x) => !(d.chosen || []).includes(x))) skipped.add(displayDecisionId(x))
    for (const x of d.expected_downstream || []) expectedDownstream.add(displayDecisionId(x))
    chronology.push({
      id: s.id,
      timestamp: s.timestamp || null,
      emitter: displayDecisionId(emitter),
      type: d.type || 'decision',
      chosen: chosenList,
      offset_ms: (() => {
        const ts = Date.parse(String(s.timestamp || ''))
        if (!Number.isFinite(ts) || !Number.isFinite(runStartMs)) return null
        return Math.max(0, Math.round(ts - runStartMs))
      })(),
    })
  }

  // Downstream should stay routing-specific. If expected_downstream is absent,
  // fall back to chosen routing/tool targets only (not all observed span identifiers).
  const downstream = expectedDownstream.size ? [...expectedDownstream] : [...chosen]

  chronology.sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')))
  return {
    stepCount: decisionSteps.length,
    primaryStepId: decisionSteps[0]?.id ?? null,
    types: [...types],
    emitters: [...emitters],
    chosen: [...chosen],
    skipped: [...skipped],
    downstream,
    chronology,
    firstTimestamp,
    lastTimestamp,
    runStartMs,
  }
}

function _resolvedAgentNameFromOpts(log, opts) {
  const byId = opts?.resolvedAgentById
  if (!byId || log?.id == null) return ''
  let raw = ''
  if (byId instanceof Map) raw = byId.get(log.id) ?? ''
  else if (typeof byId === 'object') raw = byId[log.id] ?? ''
  const s = String(raw || '').trim()
  if (!s) return ''
  return s.length > 128 ? `${s.slice(0, 127)}…` : s
}

/** Logical agent / graph node (LangGraph ``langgraph_node`` → ``metadata.agent_name``); optional manual HTTP. */
export function stepAgentName(log, opts) {
  const resolved = _resolvedAgentNameFromOpts(log, opts)
  if (resolved) return resolved
  const md = metadataObject(log)
  const v = md.agent_name ?? md.mo_agent_name
  if (v == null) return ''
  const s = String(v).trim()
  if (!s) return ''
  return s.length > 128 ? `${s.slice(0, 127)}…` : s
}

/** Top-level ``span_type`` or Tracer-style ``metadata.span_type`` (lowercase). */
export function effectiveSpanType(log) {
  const t = String(log?.span_type || '').trim().toLowerCase()
  if (t) return t
  const md = metadataObject(log)
  return String(md.span_type || '').trim().toLowerCase()
}

function contentToSnippet(content, maxLen = 56) {
  if (content == null) return ''
  let s = typeof content === 'string' ? content : JSON.stringify(content)
  s = s.replace(/\s+/g, ' ').trim()
  if (!s) return ''
  if (s.length <= maxLen) return s
  return `${s.slice(0, Math.max(1, maxLen - 1))}…`
}

function tracerPromptStepFromPrompt(prompt) {
  if (typeof prompt !== 'string') return ''
  const t = prompt.trim()
  if (!t.startsWith('{')) return ''
  try {
    const o = JSON.parse(t)
    if (o && typeof o === 'object' && o.step != null && String(o.step).trim()) {
      return String(o.step).trim()
    }
  } catch {
    /* ignore */
  }
  return ''
}

function toolNameFromToolPrompt(prompt) {
  if (typeof prompt !== 'string') return ''
  const t = prompt.trim()
  if (!t.startsWith('{')) return ''
  try {
    const o = JSON.parse(t)
    if (o && typeof o === 'object' && o.tool != null && String(o.tool).trim()) {
      return String(o.tool).trim()
    }
  } catch {
    /* ignore */
  }
  return ''
}

/** First-line-ish preview of stored LLM `response` (assistant text, not raw user question). */
function llmResponseSnippet(log) {
  const r = typeof log.response === 'string' ? log.response.trim() : ''
  if (!r) return ''
  // Skip pure JSON tool-call arrays / blobs for the main label
  if (r.startsWith('[') && r.includes('"function"')) return ''
  const first = r.split('\n').map((l) => l.trim()).find(Boolean) || ''
  if (!first) return ''
  if (first.startsWith('{') && first.length > 200) return ''
  return contentToSnippet(first, 400)
}

function lastUserMessageSnippet(log) {
  const msgs = log.messages
  if (!Array.isArray(msgs) || !msgs.length) return ''
  const userLike = (role) => {
    const r = String(role || '').toLowerCase()
    return r === 'user' || r === 'human'
  }
  const userMsgs = msgs.filter((m) => m && typeof m === 'object' && userLike(m.role))
  const pick = userMsgs.length ? userMsgs[userMsgs.length - 1] : null
  return pick ? contentToSnippet(pick.content, 400) : ''
}

/**
 * Rich one-line label for trace timelines: tool name from metadata or prompt JSON;
 * LLM: **metadata.agent_span_name** / Tracer ``prompt.step`` first (SDK human step), then
 * response preview, model, user snippet, ``span_name`` (same user question last for graphs).
 */
export function stepPrimaryLabel(log) {
  const topSt = String(log?.span_type || '').trim().toLowerCase()
  const md = metadataObject(log)
  const eff = effectiveSpanType(log)

  const isTool = topSt === 'tool' || (topSt === '' && md.tool_name != null && String(md.tool_name).trim())
  if (isTool) {
    const tn = md.tool_name
    if (tn != null && String(tn).trim()) return String(tn).trim()
    const fromPrompt = toolNameFromToolPrompt(log.prompt)
    if (fromPrompt) return fromPrompt
    return stepTitle(log)
  }

  if (eff === 'agent' || topSt === 'agent') {
    return stepTitle(log)
  }

  // llm or unknown
  const agentName = md.agent_span_name != null ? String(md.agent_span_name).trim() : ''
  if (agentName) return agentName

  const step = tracerPromptStepFromPrompt(log.prompt)
  if (step) return step

  const resp = llmResponseSnippet(log)
  if (resp) return resp

  const model = String(log.model || '').trim()
  if (model && model !== 'unknown') return model

  const userSnip = lastUserMessageSnippet(log)
  if (userSnip) return userSnip

  const sn = String(log.span_name || '').trim()
  if (sn) return sn

  return `${log.provider || '?'} · ${log.model || '?'}`
}

/** @param {unknown} raw JSON string or already-parsed object */
function handoffGotoFromPayload(raw) {
  if (raw != null && typeof raw === 'object' && !Array.isArray(raw)) {
    const g = raw.goto
    if (g != null && String(g).trim()) return String(g).trim()
    return null
  }
  if (raw == null || typeof raw !== 'string') return null
  const t = raw.trim()
  if (!t.startsWith('{')) return null
  try {
    const o = JSON.parse(t)
    if (o && typeof o === 'object' && o.goto != null) {
      const g = String(o.goto).trim()
      return g || null
    }
  } catch {
    /* ignore */
  }
  return null
}

/**
 * Parse compact handoff JSON with a ``goto`` field from tool spans: ``metadata.tool_result`` first,
 * then top-level ``response`` (object or JSON string). Clients may put the payload in either place.
 * @returns {string|null}
 */
export function handoffGotoFromToolMetadata(log) {
  const topSt = String(log?.span_type || '').trim().toLowerCase()
  const md = metadataObject(log)
  const isTool = topSt === 'tool' || (topSt === '' && md.tool_name != null && String(md.tool_name).trim())
  if (!isTool) return null
  return handoffGotoFromPayload(md.tool_result) || handoffGotoFromPayload(log?.response)
}

/**
 * Extract graph node from LangGraph-style ``__pregel_pull`` tuple string, or empty string.
 * Kept in sync with SDK ``_normalize_trace_lane_for_storage`` / LangChain callback.
 */
export function extractPregelPullLane(raw) {
  const s = raw == null ? '' : String(raw).trim()
  if (!s || !s.includes('__pregel_pull')) return ''
  const mPull = s.match(/__pregel_pull['"]?\s*,\s*['"]([^'"]+)['"]/i)
  return mPull ? String(mPull[1] || '').trim() : ''
}

/** Human-readable trace lane for UI: tuple → node name; hide ``__pregel_push`` and generic ``agent``; cap length. */
export function normalizeTraceLaneDisplay(raw) {
  const s = raw == null ? '' : String(raw).trim()
  if (!s) return ''
  if (s.includes('__pregel_push')) return ''
  const pull = extractPregelPullLane(s)
  if (pull) {
    if (pull.toLowerCase() === 'agent') return ''
    return pull.length > 28 ? `${pull.slice(0, 27)}…` : pull
  }
  return s.length > 28 ? `${s.slice(0, 27)}…` : s
}

/** Optional short lane label from ``metadata.trace_lane`` (no prompt heuristics). */
function traceLaneFromMetadata(md) {
  if (!md || typeof md !== 'object') return ''
  const v = md.trace_lane ?? md.mo_trace_lane
  return normalizeTraceLaneDisplay(v)
}

function traceStepPrefixLabel(log, opts) {
  const an = stepAgentName(log, opts)
  if (an) return an.length > 28 ? `${an.slice(0, 27)}…` : an
  const md = metadataObject(log)
  return traceLaneFromMetadata(md)
}

/**
 * One-line label for trace UIs: optional **trace_lane** prefix (ingest metadata), **handoff goto** suffix on tools (``tool_result`` / ``response`` JSON).
 * Falls back to {@link stepPrimaryLabel} when nothing applies.
 */
export function traceStepDisplayLabel(log, opts) {
  const base = stepPrimaryLabel(log)
  const topSt = String(log?.span_type || '').trim().toLowerCase()
  const md = metadataObject(log)
  const isTool = topSt === 'tool' || (topSt === '' && md.tool_name != null && String(md.tool_name).trim())
  if (isTool) {
    const pref = traceStepPrefixLabel(log, opts)
    const g = handoffGotoFromToolMetadata(log)
    let line = base
    if (g) line = `${line} → ${g}`
    if (pref) line = `${pref} · ${line}`
    return line
  }
  if (effectiveSpanType(log) === 'llm') {
    const pref = traceStepPrefixLabel(log, opts)
    if (pref) return `${pref} · ${base}`
  }
  return base
}

/**
 * When `response` is OpenAI chat-style tool_calls JSON (array of { type, function: { name } }),
 * returns tool names in order. Otherwise null.
 * Accepts string JSON, a parsed array, or `{ tool_calls: [...] }` (some APIs/clients).
 */
export function parseOpenAIToolCallsSummary(response) {
  let j = null
  if (Array.isArray(response)) {
    j = response
  } else if (response && typeof response === 'object' && Array.isArray(response.tool_calls)) {
    j = response.tool_calls
  } else if (typeof response === 'string') {
    const r = response.trim()
    if (!r.startsWith('[')) return null
    try {
      j = JSON.parse(r)
    } catch {
      return null
    }
  } else {
    return null
  }
  if (!Array.isArray(j) || j.length === 0) return null
  const names = []
  for (const item of j) {
    if (!item || typeof item !== 'object') continue
    const fn = item.function
    if (fn && typeof fn === 'object' && fn.name) {
      names.push(String(fn.name))
    }
  }
  if (names.length === 0) return null
  return { count: names.length, names }
}

/** One line for timeline UI: "3 tool calls: a, b, c (+1 more)" */
export function formatToolCallsSummaryShort(summary, { maxNames = 5 } = {}) {
  if (!summary) return ''
  const { count, names } = summary
  const shown = names.slice(0, maxNames)
  const rest = count - shown.length
  let line = `${count} tool call${count === 1 ? '' : 's'}: ${shown.join(', ')}`
  if (rest > 0) line += ` (+${rest} more)`
  return line
}

/** Short label for span_type (llm | tool | agent | …). */
export function spanTypeLabel(log) {
  const t = effectiveSpanType(log)
  if (t === 'llm') return 'LLM'
  if (t === 'tool') return 'tool'
  if (t === 'agent') return 'agent'
  return t ? t.slice(0, 12) : ''
}

/** Bar / accent color by span_type (falls back to span_name heuristics). */
export function spanTypeAccent(log) {
  const t = effectiveSpanType(log)
  if (t === 'tool') return '#f7c948'
  if (t === 'agent') return '#7c6af7'
  if (t === 'llm') return '#22d3a0'
  return spanAccent(log.span_name)
}

/** Chronological order with indent depth from parent_span_id → parent log id. */
export function stepsWithDepth(steps) {
  if (!steps?.length) return []
  const byId = Object.fromEntries(steps.map(s => [s.id, s]))
  const stepStartMs = (s) => {
    const md = metadataObject(s)
    const startedAt = Date.parse(String(md.started_at || ''))
    if (Number.isFinite(startedAt)) return startedAt
    const endedAt = Date.parse(String(md.ended_at || ''))
    if (Number.isFinite(endedAt)) {
      const lat = Number(s?.latency_ms || 0)
      if (Number.isFinite(lat)) return Math.max(0, Math.round(endedAt - lat))
      return endedAt
    }
    const ts = Date.parse(String(s?.timestamp || ''))
    if (Number.isFinite(ts)) return ts
    return Number.POSITIVE_INFINITY
  }
  function depth(s) {
    let d = 0
    let cur = s
    const seen = new Set()
    while (cur && cur.parent_span_id != null && byId[cur.parent_span_id]) {
      if (seen.has(cur.id)) break
      seen.add(cur.id)
      d++
      cur = byId[cur.parent_span_id]
      if (d > 40) break
    }
    return d
  }
  return [...steps]
    .sort((a, b) => {
      const da = stepStartMs(a)
      const db = stepStartMs(b)
      if (da !== db) return da - db
      const ta = Date.parse(String(a?.timestamp || ''))
      const tb = Date.parse(String(b?.timestamp || ''))
      if (Number.isFinite(ta) && Number.isFinite(tb) && ta !== tb) return ta - tb
      return (Number(a?.id) || 0) - (Number(b?.id) || 0)
    })
    .map(s => ({ ...s, _depth: depth(s) }))
}

export function spanAccent(name) {
  const n = (name || '').toLowerCase()
  if (n.includes('retriev')) return '#7c6af7'
  if (n.includes('tool')) return '#f7c948'
  if (n.includes('llm') || n.includes('chat')) return '#22d3a0'
  // Router / route: purple — reserve #f75f6a for errors / stuck only.
  if (n.includes('router') || n.includes('route')) return '#7c6af7'
  return '#64748b'
}

/** Prefer span_type for dot color in trees when present. */
export function stepDotColor(log) {
  if (effectiveSpanType(log)) return spanTypeAccent(log)
  return spanAccent(log.span_name)
}

/** Second line for LLM rows: assistant snippet when non-empty (after human-first primary label). */
export function llmResponseSubtitleLine(log) {
  if (effectiveSpanType(log) !== 'llm') return ''
  return llmResponseSnippet(log) || ''
}

export function buildWaterfallRows(steps) {
  let t = 0
  return steps.map((s, i) => {
    const ms = Number(s.latency_ms) || 0
    const row = {
      i: i + 1,
      name: (() => {
        const lab = traceStepDisplayLabel(s)
        return lab.slice(0, 40) + (lab.length > 40 ? '…' : '')
      })(),
      latency_ms: ms,
      t0: t,
      t1: t + ms,
    }
    t += ms
    return row
  })
}

/**
 * Root session span from the Python tracer: ``span_type`` agent, no parent, ``span_name`` router
 * (or ``metadata.agent_span_name`` like ``agent/normal``). Its ``latency_ms`` is wall time for the
 * whole session, so Gantt layouts must not pack it like a leaf step (that pushes real work to the right).
 */
export function isSessionEnvelopeSpan(log) {
  if (!log) return false
  if (effectiveSpanType(log) !== 'agent') return false
  if (log.parent_span_id != null && String(log.parent_span_id).trim() !== '') return false
  const sn = String(log.span_name || '').toLowerCase()
  if (sn === 'router') return true
  const md = metadataObject(log)
  const an = String(md.agent_span_name || '').trim().toLowerCase()
  if (an.startsWith('agent/')) return true
  return false
}

/** Parse API `timestamp` (ISO) to epoch ms, or NaN. */
export function parseStepTimestampMs(ts) {
  if (ts == null || ts === '') return NaN
  const n = Date.parse(String(ts))
  return Number.isFinite(n) ? n : NaN
}

/**
 * Build wall-clock-ish segments for a run trace map.
 * Convention: `timestamp` is treated as **span end** (ingest / row time); bar is [end - latency, end].
 * If timestamps are missing, invalid, or **all identical** (common for one batched flush), falls back to
 * the same sequential packing as `buildWaterfallRows` so bars remain readable.
 *
 * Root session spans (see {@link isSessionEnvelopeSpan}) are **not** packed as a long first block:
 * real work spans are laid out first, then the session envelope bar spans their union so children are
 * not drawn as if they ran after the whole trace finished.
 *
 * Swimlanes: spans with ``metadata.agent_name`` (e.g. LangGraph ``langgraph_node``) are grouped
 * vertically by that value; within each agent, overlapping wall-clock intervals still get extra rows.
 * Spans without ``agent_name`` share one ``__none__`` band.
 *
 * @returns {{ segments: Array<{
 *   id: number,
 *   step: object,
 *   startMs: number,
 *   endMs: number,
 *   relStart: number,
 *   relEnd: number,
 *   lane: number,
 *   label: string,
 *   span_type: string,
 *   parent_span_id: number|null,
 *   sessionEnvelope?: boolean
 * }>, rangeMs: number, usedSequentialFallback: boolean }}
 */
function _segmentRow(s, startMs, endMs, sessionEnvelope = false, opts = null) {
  const resolvedAgentName = stepAgentName(s, opts)
  return {
    id: s.id,
    step: s,
    startMs,
    endMs,
    label: traceStepDisplayLabel(s, opts),
    span_type: String(s.span_type || '').trim().toLowerCase(),
    parent_span_id: s.parent_span_id != null ? Number(s.parent_span_id) : null,
    sessionEnvelope: sessionEnvelope || undefined,
    resolvedAgentName: resolvedAgentName || undefined,
  }
}

const _AGENT_GROUP_SESSION = '__session__'
const _AGENT_GROUP_NONE = '__none__'

function _agentGroupKey(seg) {
  if (seg.sessionEnvelope) return _AGENT_GROUP_SESSION
  const n = seg.resolvedAgentName || stepAgentName(seg.step)
  return n || _AGENT_GROUP_NONE
}

function _pushUniqueGroup(order, seen, key) {
  if (seen.has(key)) return
  seen.add(key)
  order.push(key)
}

/**
 * Vertical swimlanes: one block of rows per distinct ``metadata.agent_name`` (session envelope
 * first, then first-seen order). Within each agent, rows still pack non-overlapping intervals.
 */
function _assignLanesGroupedByAgentName(normalized) {
  const byStart = [...normalized].sort((a, b) => {
    if (a.startMs !== b.startMs) return a.startMs - b.startMs
    const ae = a.sessionEnvelope ? 1 : 0
    const be = b.sessionEnvelope ? 1 : 0
    if (ae !== be) return be - ae
    return a.endMs - b.endMs
  })
  const groupOrder = []
  const seen = new Set()
  for (const seg of byStart) {
    if (seg.sessionEnvelope) _pushUniqueGroup(groupOrder, seen, _AGENT_GROUP_SESSION)
  }
  for (const seg of byStart) {
    const k = _agentGroupKey(seg)
    if (k !== _AGENT_GROUP_SESSION) _pushUniqueGroup(groupOrder, seen, k)
  }
  let base = 0
  for (const key of groupOrder) {
    const groupSegs = normalized.filter((s) => _agentGroupKey(s) === key)
    groupSegs.sort((a, b) => {
      if (a.startMs !== b.startMs) return a.startMs - b.startMs
      const ae = a.sessionEnvelope ? 1 : 0
      const be = b.sessionEnvelope ? 1 : 0
      if (ae !== be) return be - ae
      return a.endMs - b.endMs
    })
    const trackEnds = []
    for (const seg of groupSegs) {
      let placed = false
      for (let i = 0; i < trackEnds.length; i++) {
        if (trackEnds[i] <= seg.startMs) {
          seg.lane = base + i
          trackEnds[i] = seg.endMs
          placed = true
          break
        }
      }
      if (!placed) {
        seg.lane = base + trackEnds.length
        trackEnds.push(seg.endMs)
      }
    }
    base += Math.max(1, trackEnds.length)
  }
}

/**
 * Build [startMs,endMs] rows for ``work`` only (used when session envelope spans are split out).
 * @returns {{ raw: Array<object>, usedSequentialFromInvalidTs: boolean }}
 */
function _rawSegmentsForWorkhorse(work, degenerateClock, opts = null) {
  const raw = []
  let usedSequentialFromInvalidTs = false
  if (!work.length) return { raw, usedSequentialFromInvalidTs }
  if (degenerateClock) {
    let acc = 0
    for (const s of work) {
      const lat = Math.max(1, Number(s.latency_ms) || 0)
      raw.push(_segmentRow(s, acc, acc + lat, false, opts))
      acc += lat + 2
    }
    return { raw, usedSequentialFromInvalidTs }
  }
  const tsMs = work.map((s) => parseStepTimestampMs(s.timestamp))
  for (let i = 0; i < work.length; i++) {
    const s = work[i]
    const lat = Math.max(0, Number(s.latency_ms) || 0)
    const endMs = tsMs[i]
    let startMs = endMs - lat
    if (!Number.isFinite(endMs)) {
      usedSequentialFromInvalidTs = true
      const prev = raw[raw.length - 1]
      const base = prev ? prev.endMs + 2 : 0
      raw.push(_segmentRow(s, base, base + Math.max(1, lat), false, opts))
      continue
    }
    if (!Number.isFinite(startMs)) startMs = endMs - Math.max(1, lat)
    if (startMs > endMs) startMs = endMs - Math.max(1, lat)
    if (startMs === endMs) startMs = endMs - 1
    raw.push(_segmentRow(s, startMs, endMs, false, opts))
  }
  return { raw, usedSequentialFromInvalidTs }
}

export function stepsToGanttSegments(steps, opts = null) {
  if (!steps?.length) {
    return { segments: [], rangeMs: 0, usedSequentialFallback: false }
  }
  const ordered = [...steps].sort((a, b) => {
    const c = String(a.timestamp || '').localeCompare(String(b.timestamp || ''))
    if (c !== 0) return c
    return (Number(a.id) || 0) - (Number(b.id) || 0)
  })

  const tsMs = ordered.map((s) => parseStepTimestampMs(s.timestamp))
  const finite = tsMs.filter((t) => Number.isFinite(t))
  const uniqueTs = new Set(finite)
  const degenerateClock =
    finite.length === 0 ||
    (ordered.length > 1 && uniqueTs.size <= 1) ||
    (finite.length > 1 &&
      Math.max(...finite) - Math.min(...finite) < 2 &&
      ordered.reduce((a, s) => a + Math.max(0, Number(s.latency_ms) || 0), 0) > 5)

  let usedSequentialFallback = degenerateClock
  const envelopes = ordered.filter(isSessionEnvelopeSpan)
  const work = ordered.filter((s) => !isSessionEnvelopeSpan(s))

  let raw = []
  if (work.length === 0) {
    const { raw: r0, usedSequentialFromInvalidTs } = _rawSegmentsForWorkhorse(ordered, degenerateClock, opts)
    raw = r0
    if (usedSequentialFromInvalidTs) usedSequentialFallback = true
  } else if (envelopes.length === 0) {
    const { raw: r0, usedSequentialFromInvalidTs } = _rawSegmentsForWorkhorse(ordered, degenerateClock, opts)
    raw = r0
    if (usedSequentialFromInvalidTs) usedSequentialFallback = true
  } else {
    const { raw: rawWork, usedSequentialFromInvalidTs } = _rawSegmentsForWorkhorse(work, degenerateClock, opts)
    if (usedSequentialFromInvalidTs) usedSequentialFallback = true
    const envSteps = [...envelopes].sort((a, b) => (Number(a.id) || 0) - (Number(b.id) || 0))
    const envStep = envSteps[0]
    if (rawWork.length) {
      const mn = Math.min(...rawWork.map((r) => r.startMs))
      const mx = Math.max(...rawWork.map((r) => r.endMs))
      raw = [_segmentRow(envStep, mn, mx, true, opts), ...rawWork]
    } else {
      let acc = 0
      for (const s of envSteps) {
        const lat = Math.max(1, Number(s.latency_ms) || 0)
        raw.push(_segmentRow(s, acc, acc + lat, true, opts))
        acc += lat + 2
      }
    }
  }

  const minT = Math.min(...raw.map((r) => r.startMs))
  const maxT = Math.max(...raw.map((r) => r.endMs))
  const rangeMs = Math.max(maxT - minT, 1)

  const normalized = raw.map((r) => ({
    ...r,
    relStart: r.startMs - minT,
    relEnd: r.endMs - minT,
  }))

  _assignLanesGroupedByAgentName(normalized)

  return { segments: normalized, rangeMs, usedSequentialFallback }
}

/** Same logic as backend run_utils.cache_breakdown_for_run (fallback when API omits cache_breakdown). */
export function computeCacheBreakdownFromSteps(steps, opts = null) {
  if (!steps?.length) return null
  const rows = []
  const totals = {
    cached: 0,
    uncached: 0,
    output: 0,
    prompt_tokens: 0,
    cost_usd: 0,
  }
  for (const s of steps) {
    const inp = Number(s.input_tokens) || 0
    let cached = Number(s.cached_input_tokens) || 0
    if (cached === 0 && s.metadata && typeof s.metadata === 'object') {
      cached = Number(s.metadata.cache_read_tokens || s.metadata.cache_read) || 0
    }
    cached = Math.max(0, Math.min(cached, inp))
    const uncached = Math.max(0, inp - cached)
    const out = Number(s.output_tokens) || 0
    const cost = Number(s.cost_usd) || 0
    const barTotal = cached + uncached + out
    const cachePct = inp > 0 ? Math.round((cached / inp) * 10000) / 100 : null
    rows.push({
      id: s.id,
      label: traceStepDisplayLabel(s, opts),
      cached,
      uncached,
      output: out,
      prompt_tokens: inp,
      cache_pct: cachePct,
      cost_usd: cost,
      bar_total: barTotal,
    })
    totals.cached += cached
    totals.uncached += uncached
    totals.output += out
    totals.prompt_tokens += inp
    totals.cost_usd += cost
  }
  const pt = totals.prompt_tokens
  totals.cache_pct = pt > 0 ? Math.round((totals.cached / pt) * 10000) / 100 : null
  totals.has_cached_prompt_data = totals.cached > 0
  return { totals, rows }
}
