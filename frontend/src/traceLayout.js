import {
  stepAgentName,
  effectiveSpanType,
  isSessionEnvelopeSpan,
  handoffGotoFromToolMetadata,
  metadataObject,
  extractPregelPullLane,
} from './runUi.js'

/** Ungrouped spans (no resolved agent_name) share this column key and label. */
export const TRACE_OTHER_AGENT = '—'

/**
 * Optional extra agent names for pipeline handoffs and supervisor routing (substring rules).
 * Empty by default — only names from the run (``metadata.agent_name`` / ``mo_agent_name``) are used.
 * Set in code or re-export from a thin app-specific module if you need names that never appear on spans.
 */
export const TRACE_HASH_AGENT = []

/** Cycle accents aligned with trace UI: purple, green, amber, brown, slate. */
export const TRACE_AGENT_PALETTE = [
  { accent: '#7c6af7', border: 'rgba(124, 106, 247, 0.35)', dimBg: 'rgba(124, 106, 247, 0.08)' },
  { accent: '#22d3a0', border: 'rgba(34, 211, 160, 0.35)', dimBg: 'rgba(34, 211, 160, 0.08)' },
  { accent: '#f7c948', border: 'rgba(247, 201, 72, 0.45)', dimBg: 'rgba(247, 201, 72, 0.12)' },
  { accent: '#8b5c0a', border: 'rgba(139, 92, 10, 0.35)', dimBg: 'rgba(139, 92, 10, 0.08)' },
  { accent: '#64748b', border: 'rgba(100, 116, 139, 0.35)', dimBg: 'rgba(100, 116, 139, 0.08)' },
]

export function traceAgentStyle(index) {
  return TRACE_AGENT_PALETTE[index % TRACE_AGENT_PALETTE.length]
}

/**
 * Map handoff ``goto`` string to a column agent name when possible.
 */
export function matchHandoffTargetToAgent(goto, agents) {
  if (goto == null || !agents?.length) return null
  const g = String(goto).trim()
  if (!g) return null
  if (agents.includes(g)) return g
  const gl = g.toLowerCase().replace(/\s+/g, '_')
  const sorted = [...agents].filter((a) => a && a !== TRACE_OTHER_AGENT).sort((a, b) => String(b).length - String(a).length)
  for (const a of sorted) {
    const al = a.toLowerCase()
    if (al === gl) return a
    if (gl.includes(al) || al.includes(gl)) return a
  }
  return null
}

/**
 * Single-token first-line supervisor replies (e.g. ``research_expert``) become route keys — no fixed agent list.
 * Ignores short tokens without ``_`` to reduce noise from stray one-word answers.
 */
function slugTokensFromSupervisorResponses(work) {
  const found = new Set()
  for (const s of work || []) {
    if (effectiveSpanType(s) !== 'llm') continue
    const an = (stepAgentName(s) || '').trim()
    const pull = tracePullNodeName(s)
    if (an !== 'supervisor' && pull !== 'supervisor') continue
    const line = firstLineText(s.response).trim()
    if (!line || line.toLowerCase().startsWith('tool_call:')) continue
    if (/^(end|finish|finished)$/i.test(line)) continue
    if (!/^[a-z][a-z0-9_]+$/i.test(line)) continue
    const low = line.toLowerCase()
    if (low.length < 6 && !low.includes('_')) continue
    found.add(low)
  }
  return [...found]
}

/** Names used for handoff tools and implicit supervisor routing (longest-first for substring safety). */
function routeAgentNamesForTrace(work) {
  const out = []
  const seen = new Set()
  const push = (s) => {
    const t = String(s || '').trim()
    if (!t || t === TRACE_OTHER_AGENT || seen.has(t)) return
    seen.add(t)
    out.push(t)
  }
  for (const a of TRACE_HASH_AGENT) push(a)
  for (const s of work || []) push(stepAgentName(s))
  for (const slug of slugTokensFromSupervisorResponses(work)) push(slug)
  return out.sort((a, b) => String(b).length - String(a).length)
}

function toolDisplayName(step) {
  const md = metadataObject(step)
  const n = md.tool_name != null ? String(md.tool_name).trim() : ''
  if (n) return n
  const p = typeof step.prompt === 'string' ? step.prompt.trim() : ''
  if (p.startsWith('{')) {
    try {
      const o = JSON.parse(p)
      if (o && typeof o === 'object' && o.tool != null) return String(o.tool).trim()
    } catch {
      /* ignore */
    }
  }
  return 'tool'
}

/**
 * Handoff tool if the tool name contains a configured / discovered agent name
 * (e.g. ``transfer_to_research_expert``), not only a ``transfer`` prefix.
 * @param {object} step
 * @param {string[]} [routeAgents] from ``buildAgentTraceLayout``; if omitted, uses ``TRACE_HASH_AGENT`` (often empty).
 */
export function isHandoffToolStep(step, routeAgents) {
  const n = toolDisplayName(step)
  if (!n) return false
  const nl = n.toLowerCase()
  const list = routeAgents?.length ? routeAgents : [...TRACE_HASH_AGENT]
  return list.some((a) => {
    if (!a || a === TRACE_OTHER_AGENT) return false
    return nl.includes(String(a).toLowerCase())
  })
}

/** LangGraph-style ``trace_lane`` tuple: node name from ``__pregel_pull`` (shared with ``runUi``). */
function tracePullNodeName(step) {
  return extractPregelPullLane(metadataObject(step).trace_lane ?? metadataObject(step).mo_trace_lane ?? '')
}

function firstLineText(s) {
  if (s == null) return ''
  const t = String(s).trim()
  if (!t) return ''
  return t.split('\n').map((l) => l.trim()).find(Boolean) || ''
}

/**
 * First routing line from supervisor text: match against ``routeAgents`` (substring, longest first).
 * Returns ``end`` for finish tokens, else the matched agent key or ``''``.
 */
function parseSupervisorRoute(response, routeAgents) {
  const raw = firstLineText(response)
  if (!raw || raw.toLowerCase().startsWith('tool_call:')) return ''
  const low = raw.toLowerCase()
  const endTok = /^(end|finish|finished)\b/i.test(raw.trim())
  if (endTok) return 'end'
  const agents = routeAgents?.length ? routeAgents : [...TRACE_HASH_AGENT]
  const hit = agents.find((a) => a && low.includes(String(a).toLowerCase()))
  return hit || ''
}

/**
 * @param {object[]} steps - run detail steps from API
 * @returns {{
 *   workSteps: object[],
 *   agents: string[],
 *   blocksByAgent: Map<string, { llm: object, tools: object[] }[]>,
 *   resolvedAgent: Map<string|number, string>,
 *   handoffEdges: { toolId?: string|number, sourceLlmId?: string|number, sourceAgent: string, targetAgent: string, targetLlmId: string|number|null }[],
 *   firstLlmIdByAgent: Map<string, string|number|null>,
 *   finalLlm: object|null,
 *   routeAgents: string[],
 * }}
 */
export function buildAgentTraceLayout(steps) {
  const empty = {
    workSteps: [],
    agents: [],
    blocksByAgent: new Map(),
    resolvedAgent: new Map(),
    handoffEdges: [],
    firstLlmIdByAgent: new Map(),
    finalLlm: null,
    routeAgents: [],
  }
  if (!steps?.length) return empty

  const ordered = [...steps].sort((a, b) => {
    const c = String(a.timestamp || '').localeCompare(String(b.timestamp || ''))
    if (c !== 0) return c
    return (Number(a.id) || 0) - (Number(b.id) || 0)
  })

  const work = ordered.filter(
    (s) => !isSessionEnvelopeSpan(s) && effectiveSpanType(s) !== 'agent',
  )

  const routeAgents = routeAgentNamesForTrace(work)

  let lastLlmAgent = ''
  /** Set from supervisor routing for the next worker LLM(s) in LangGraph-style runs. */
  let pendingWorker = ''
  const resolved = new Map()
  for (const s of work) {
    const t = effectiveSpanType(s)
    if (t === 'llm') {
      let an = (stepAgentName(s) || '').trim()
      const pullNode = tracePullNodeName(s)
      if (!an && pullNode === 'supervisor') an = 'supervisor'

      if (an === 'supervisor') {
        const route = parseSupervisorRoute(s.response, routeAgents)
        if (route === 'end') pendingWorker = ''
        else if (route) pendingWorker = route
        resolved.set(s.id, 'supervisor')
        lastLlmAgent = 'supervisor'
      } else if (!an) {
        const worker = pendingWorker || ''
        resolved.set(s.id, worker)
        lastLlmAgent = worker
      } else {
        resolved.set(s.id, an)
        lastLlmAgent = an
      }
    } else if (t === 'tool') {
      resolved.set(s.id, (stepAgentName(s) || '').trim() || lastLlmAgent || '')
    } else {
      resolved.set(s.id, (stepAgentName(s) || '').trim() || lastLlmAgent || '')
    }
  }

  let hasEmpty = false
  for (const s of work) {
    if (!(resolved.get(s.id) || '').trim()) hasEmpty = true
  }
  if (hasEmpty) {
    for (const s of work) {
      if (!(resolved.get(s.id) || '').trim()) resolved.set(s.id, TRACE_OTHER_AGENT)
    }
  }

  const agents = []
  const seen = new Set()
  for (const s of work) {
    const a = resolved.get(s.id)
    if (a && !seen.has(a)) {
      seen.add(a)
      agents.push(a)
    }
  }

  const blocksByAgent = new Map()
  for (const a of agents) blocksByAgent.set(a, [])

  for (const a of agents) {
    const mine = work.filter((s) => resolved.get(s.id) === a)
    const blocks = []
    let i = 0
    while (i < mine.length) {
      const s = mine[i]
      if (effectiveSpanType(s) === 'llm') {
        const block = { llm: s, tools: [] }
        i += 1
        while (i < mine.length && effectiveSpanType(mine[i]) === 'tool') {
          block.tools.push(mine[i])
          i += 1
        }
        blocks.push(block)
      } else {
        i += 1
      }
    }
    blocksByAgent.set(a, blocks)
  }

  const firstLlmIdByAgent = new Map()
  for (const a of agents) {
    const blocks = blocksByAgent.get(a) || []
    firstLlmIdByAgent.set(a, blocks.length ? blocks[0].llm.id : null)
  }

  const handoffEdges = []
  for (const a of agents) {
    const blocks = blocksByAgent.get(a) || []
    for (const block of blocks) {
      for (const tool of block.tools) {
        if (!isHandoffToolStep(tool, routeAgents)) continue
        const goto = handoffGotoFromToolMetadata(tool) || toolDisplayName(tool)
        const targetAgent = matchHandoffTargetToAgent(goto || '', agents)
        if (!targetAgent || targetAgent === a) continue
        const targetLlmId = firstLlmIdByAgent.get(targetAgent) ?? null
        handoffEdges.push({
          toolId: tool.id,
          sourceAgent: a,
          targetAgent,
          targetLlmId,
        })
      }
    }
  }

  // Implicit routing arrows when ingest has no transfer_* tool rows (supervisor text → next worker LLM).
  for (let wi = 0; wi < work.length; wi += 1) {
    const cur = work[wi]
    if (effectiveSpanType(cur) !== 'llm') continue
    if (resolved.get(cur.id) !== 'supervisor') continue
    const route = parseSupervisorRoute(cur.response, routeAgents)
    if (!route || route === 'end' || !routeAgents.includes(route)) continue
    for (let j = wi + 1; j < work.length; j += 1) {
      const nxt = work[j]
      if (effectiveSpanType(nxt) !== 'llm') continue
      if (resolved.get(nxt.id) === route) {
        handoffEdges.push({
          sourceLlmId: cur.id,
          sourceAgent: 'supervisor',
          targetAgent: route,
          targetLlmId: nxt.id,
        })
        break
      }
    }
  }

  const llmSpans = work.filter((s) => effectiveSpanType(s) === 'llm')
  let finalLlm = null
  for (let i = llmSpans.length - 1; i >= 0; i -= 1) {
    const s = llmSpans[i]
    const r = String(s.response || '').trim()
    if (r && !r.toLowerCase().startsWith('tool_call:')) {
      finalLlm = s
      break
    }
  }

  return {
    workSteps: work,
    agents,
    blocksByAgent,
    resolvedAgent: resolved,
    handoffEdges,
    firstLlmIdByAgent,
    finalLlm,
    routeAgents,
  }
}
