import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  buildAgentTraceLayout,
  traceAgentStyle,
  TRACE_OTHER_AGENT,
  isHandoffToolStep,
  matchHandoffTargetToAgent,
} from '../traceLayout.js'
import { metadataObject, handoffGotoFromToolMetadata } from '../runUi.js'

function tryParseJson(text) {
  if (text == null || typeof text !== 'string') return null
  const t = text.trim()
  if (!t.length) return null
  if (t[0] !== '{' && t[0] !== '[') return null
  try {
    return JSON.parse(t)
  } catch {
    return null
  }
}

function normalizedChatMessages(log) {
  const req = log?.request
  if (req && typeof req === 'object' && Array.isArray(req.messages) && req.messages.length) {
    return req.messages
  }
  const raw = log?.messages
  if (Array.isArray(raw) && raw.length) return raw
  if (typeof raw === 'string' && raw.trim().startsWith('[')) {
    try {
      const p = JSON.parse(raw)
      return Array.isArray(p) && p.length ? p : null
    } catch {
      return null
    }
  }
  return null
}

function toolNameFromStep(step) {
  const md = metadataObject(step)
  const n = md.tool_name != null ? String(md.tool_name).trim() : ''
  if (n) return n
  const p = typeof step.prompt === 'string' ? step.prompt.trim() : ''
  if (p.startsWith('{')) {
    const o = tryParseJson(p)
    if (o && typeof o === 'object' && o.tool != null) return String(o.tool).trim()
  }
  return 'tool'
}

function toolArgsShort(step) {
  const md = metadataObject(step)
  const args = md.tool_args
  if (!args || typeof args !== 'object') return ''
  return Object.entries(args)
    .map(([k, v]) => {
      const vs = String(v)
      return `${k}: ${vs.length > 12 ? `${vs.slice(0, 12)}…` : vs}`
    })
    .join(' · ')
}

function firstLine(s) {
  if (s == null) return ''
  const t = String(s).trim()
  if (!t) return ''
  return t.split('\n').map((l) => l.trim()).find(Boolean) || ''
}

function isRoutingDecisionLlm(agentName, llmStep) {
  if (agentName === TRACE_OTHER_AGENT) return false
  const an = String(agentName).toLowerCase()
  const looksSupervisor = an.includes('supervisor') || an === 'router' || an === 'orchestrator'
  if (!looksSupervisor) return false
  const raw = firstLine(llmStep.response).trim()
  if (!raw || raw.toLowerCase().startsWith('tool_call:')) return false
  const r = raw.toLowerCase()
  if (/^(end|finish|finished|router)\b/.test(r)) return true
  if (/^[a-z][a-z0-9_]+$/i.test(raw) && (r.length >= 6 || r.includes('_'))) return true
  return false
}

function triggerSnippetForLlm(agentName, llmStep, messages) {
  const resp = firstLine(llmStep.response)
  if (isRoutingDecisionLlm(agentName, llmStep) && messages?.length) {
    const lastAsst = [...messages].reverse().find((m) => m.role === 'assistant' && String(m.content || '').trim() && !m.tool_calls)
    const lastUser = [...messages].reverse().find((m) => m.role === 'user' || m.role === 'human')
    const pick = lastAsst || lastUser
    if (pick?.content) {
      const t = String(pick.content).replace(/\s+/g, ' ').trim()
      return t.length > 140 ? `${t.slice(0, 139)}…` : t
    }
  }
  if (messages?.length) {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user' || m.role === 'human')
    if (lastUser?.content) {
      const t = String(lastUser.content).replace(/\s+/g, ' ').trim()
      return t.length > 140 ? `${t.slice(0, 139)}…` : t
    }
  }
  return ''
}

function MessageRows({ messages, prevCount }) {
  const [histOpen, setHistOpen] = useState(false)
  if (!messages?.length) return null
  const prev = Math.max(0, Math.min(prevCount, messages.length))
  const row = (m, i) => {
    const role = String(m.role || '').toLowerCase()
    const roleClass =
      role === 'system'
        ? 'text-muted italic'
        : role === 'user' || role === 'human'
          ? 'text-emerald-700'
          : role === 'assistant'
            ? 'text-sky-600'
            : 'text-muted'
    const label = role === 'human' ? 'user' : role
    return (
      <div key={i} className="flex gap-2 py-1.5 border-b border-line/60 last:border-0 text-[11px]">
        <div className={`w-16 shrink-0 font-mono uppercase text-[9px] pt-0.5 ${roleClass}`}>{label}</div>
        <div className="min-w-0 flex-1 text-muted break-words">
          {m.tool_calls?.length ? (
            <div className="flex flex-wrap gap-1">
              {m.tool_calls.map((tc, j) => {
                const name = tc?.function?.name || tc?.name || 'call'
                const args = tc?.function?.args ?? tc?.args
                const as =
                  args && typeof args === 'object'
                    ? `(${Object.entries(args)
                        .map(([k, v]) => {
                          const vs = String(v)
                          return `${k}: ${vs.length > 10 ? `${vs.slice(0, 10)}…` : vs}`
                        })
                        .join(', ')})`
                    : '()'
                return (
                  <span
                    key={j}
                    className="inline-flex items-center rounded-full border border-line bg-surface px-2 py-0.5 text-[10px] text-ink"
                  >
                    {name}
                    {as}
                  </span>
                )
              })}
            </div>
          ) : null}
          {m.content != null && String(m.content).trim() && !m.tool_calls?.length ? (
            <div>{String(m.content)}</div>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className="border-t border-line px-3 py-2 bg-inset/50">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-muted text-[9px] font-mono uppercase tracking-widest">prompt</span>
        <span className="text-[9px] text-muted border border-line rounded px-1.5 py-0.5 font-mono">
          {prev === 0 ? `${messages.length} messages` : `+${messages.length - prev} new · ${messages.length} total`}
        </span>
      </div>
      {prev > 0 && (
        <>
          <button
            type="button"
            className="flex items-center gap-2 py-1 text-[10px] text-muted hover:text-ink font-mono w-full text-left border-b border-line/60 mb-1"
            onClick={() => setHistOpen((o) => !o)}
          >
            <span className="inline-block transition-transform" style={{ transform: histOpen ? 'rotate(90deg)' : '' }}>
              ▸
            </span>
            {`${prev} earlier messages`}
          </button>
          {histOpen && (
            <div className="opacity-50 mb-2 space-y-0">{messages.slice(0, prev).map((m, i) => row(m, `h-${i}`))}</div>
          )}
          <div className="h-px bg-line/80 my-1" />
        </>
      )}
      <div>{messages.slice(prev).map((m, i) => row(m, `n-${i}`))}</div>
    </div>
  )
}

function offsetWithin(el, ancestor) {
  let x = 0
  let y = 0
  let cur = el
  while (cur && cur !== ancestor) {
    x += cur.offsetLeft
    y += cur.offsetTop
    cur = cur.offsetParent
  }
  return { x, y }
}

function drawHandoffArrow(svg, wrap, fromEl, toEl, colorHex) {
  if (!svg || !wrap || !fromEl || !toEl) return
  const fp = offsetWithin(fromEl, wrap)
  const tp = offsetWithin(toEl, wrap)
  const sx = fp.x + fromEl.offsetWidth
  const sy = fp.y + fromEl.offsetHeight / 2
  const dx = tp.x
  const dy = tp.y + Math.min(24, toEl.offsetHeight / 2)
  const mx = (sx + dx) / 2
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
  path.setAttribute('d', `M ${sx} ${sy} C ${mx} ${sy}, ${mx} ${dy}, ${dx} ${dy}`)
  path.setAttribute('fill', 'none')
  path.setAttribute('stroke', colorHex)
  path.setAttribute('stroke-width', '1.5')
  path.setAttribute('opacity', '0.45')
  const right = dx > sx
  const tip = right
    ? `${dx},${dy} ${dx - 8},${dy - 4} ${dx - 8},${dy + 4}`
    : `${dx},${dy} ${dx + 8},${dy - 4} ${dx + 8},${dy + 4}`
  const head = document.createElementNS('http://www.w3.org/2000/svg', 'polygon')
  head.setAttribute('points', tip)
  head.setAttribute('fill', colorHex)
  head.setAttribute('opacity', '0.5')
  svg.appendChild(path)
  svg.appendChild(head)
}

/**
 * @param {{
 *   steps: object[],
 *   runKey: string,
 *   onOpenLog?: (id: string|number, opts: object) => void,
 *   rangeMs?: number,
 *   totalTokens?: number,
 *   spanCount?: number,
 *   anyError?: boolean,
 * }} props
 */
export default function RunTraceV2({
  steps,
  runKey,
  onOpenLog,
  rangeMs = 0,
  totalTokens = 0,
  spanCount = 0,
  anyError = false,
}) {
  const layout = useMemo(() => buildAgentTraceLayout(steps || []), [steps])
  const { agents, blocksByAgent, handoffEdges, finalLlm, routeAgents } = layout

  const [openKey, setOpenKey] = useState(null)
  const scrollRef = useRef(null)
  const elRefs = useRef({})
  const setRef = useCallback((key, el) => {
    if (el) elRefs.current[key] = el
    else delete elRefs.current[key]
  }, [])

  const redrawArrows = useCallback(() => {
    const wrap = scrollRef.current
    if (!wrap) return
    const svg = wrap.querySelector('[data-trace-arrows]')
    if (!svg || !(svg instanceof SVGSVGElement)) return
    while (svg.firstChild) svg.removeChild(svg.firstChild)
    svg.setAttribute('width', String(wrap.scrollWidth))
    svg.setAttribute('height', String(wrap.scrollHeight))

    for (const edge of handoffEdges) {
      const fromEl =
        edge.sourceLlmId != null
          ? elRefs.current[`llm-${edge.sourceLlmId}`]
          : elRefs.current[`tool-${edge.toolId}`]
      const toKey = edge.targetLlmId != null ? `llm-${edge.targetLlmId}` : `colhead-${edge.targetAgent}`
      const toEl = elRefs.current[toKey]
      const ti = agents.indexOf(edge.targetAgent)
      const color = ti >= 0 ? traceAgentStyle(ti).accent : '#7c6af7'
      if (fromEl && toEl) drawHandoffArrow(svg, wrap, fromEl, toEl, color)
    }
  }, [agents, handoffEdges])

  useLayoutEffect(() => {
    redrawArrows()
  }, [redrawArrows, openKey, steps])

  useEffect(() => {
    const wrap = scrollRef.current
    if (!wrap) return
    const ro = new ResizeObserver(() => redrawArrows())
    ro.observe(wrap)
    const onScroll = () => redrawArrows()
    wrap.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      ro.disconnect()
      wrap.removeEventListener('scroll', onScroll)
    }
  }, [redrawArrows])

  if (!steps?.length) {
    return <div className="p-4 text-sm text-muted font-mono">No steps.</div>
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-page text-ink">
      <div className="shrink-0 border-b border-line bg-surface px-4 py-2 flex flex-wrap items-center gap-4 text-[11px] text-muted font-mono">
        <div className="flex items-center gap-2">
          <span className="rounded border border-line bg-inset px-1.5 py-0.5 text-[10px] uppercase tracking-widest">llm</span>
          <span className="h-2 w-5 rounded-sm bg-[#22d3a0]/25 border border-[#22d3a0]/40" />
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded border border-line bg-inset px-1.5 py-0.5 text-[10px] uppercase tracking-widest">tool</span>
          <span className="h-2 w-5 rounded-full bg-[#f7c948]/20 border border-[#d4a826]/45" />
        </div>
        <div className="w-px h-3 bg-line" />
        {agents.map((a, i) => {
          const pal = traceAgentStyle(i)
          return (
            <div key={a} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full shrink-0" style={{ background: pal.accent }} />
              <span className="truncate max-w-[10rem]">{a === TRACE_OTHER_AGENT ? '—' : a}</span>
            </div>
          )
        })}
      </div>

      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto p-3">
        <svg
          data-trace-arrows
          className="pointer-events-none absolute left-0 top-0 z-20 overflow-visible"
          aria-hidden
        />
        <div className="mx-auto inline-flex gap-6 min-w-max items-start pb-8 px-3">
          {agents.map((agentName, colIdx) => {
            const pal = traceAgentStyle(colIdx)
            const blocks = blocksByAgent.get(agentName) || []
            const llmCnt = blocks.length
            const toolCnt = blocks.reduce(
              (n, b) => n + b.tools.filter((t) => !isHandoffToolStep(t, routeAgents)).length,
              0,
            )
            return (
              <div key={agentName} className="flex shrink-0 w-[min(24rem,42vw)] flex-col border border-line rounded-lg overflow-hidden bg-surface shadow-sm">
                <div
                  ref={(el) => setRef(`colhead-${agentName}`, el)}
                  className="flex items-center gap-2 px-3 py-2.5 border-b border-line bg-inset/40"
                >
                  <span className="w-0.5 h-4 rounded-sm shrink-0" style={{ background: pal.accent }} />
                  <span className="font-mono text-xs font-medium truncate" style={{ color: pal.accent }}>
                    {agentName === TRACE_OTHER_AGENT ? '—' : agentName}
                  </span>
                  <span className="ml-auto text-[10px] text-muted font-mono flex gap-2 shrink-0">
                    <span>{llmCnt} llm</span>
                    <span>{toolCnt} tool</span>
                  </span>
                </div>
                <div className="flex flex-col min-h-[120px]">
                  {(() => {
                    let msgCum = 0
                    return blocks.map((block, bi) => {
                    const llm = block.llm
                    const msgs = normalizedChatMessages(llm)
                    const prevMsgCount = msgCum
                    msgCum += msgs?.length || 0
                    const open = openKey === `llm-${llm.id}`
                    const routing = isRoutingDecisionLlm(agentName, llm)
                    const resp = firstLine(llm.response)
                    const isFinal = finalLlm && String(finalLlm.id) === String(llm.id)
                    const snippet = triggerSnippetForLlm(agentName, llm, msgs)

                    return (
                      <div
                        key={llm.id}
                        ref={(el) => setRef(`llm-${llm.id}`, el)}
                        data-span-id={llm.id}
                        className={`border-b border-line last:border-b-0 ${open ? 'bg-[#7c6af7]/[0.04]' : ''}`}
                      >
                        <button
                          type="button"
                          className="w-full text-left px-3 py-2.5 hover:bg-inset/80 transition-colors"
                          onClick={() => {
                            setOpenKey((k) => (k === `llm-${llm.id}` ? null : `llm-${llm.id}`))
                            setTimeout(redrawArrows, 0)
                          }}
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-[9px] font-mono uppercase tracking-widest text-muted border border-line rounded px-1 py-0.5">
                              llm
                            </span>
                            <span className="text-xs font-mono text-ink truncate">{llm.model || '—'}</span>
                            {isFinal && (
                              <span className="text-[9px] font-mono uppercase tracking-wide text-muted border border-line rounded px-1.5 py-0.5 ml-auto">
                                final
                              </span>
                            )}
                            <span className="text-[10px] text-muted font-mono shrink-0 ml-auto">
                              {Number(llm.latency_ms || 0).toFixed(0)}ms
                            </span>
                          </div>
                          {routing && resp && (
                            <div className="text-xs font-medium mb-1 truncate" style={{ color: pal.accent }}>
                              → {resp}
                            </div>
                          )}
                          {snippet && <div className="text-[11px] text-ink line-clamp-2 mb-1">{snippet}</div>}
                          <div className="text-[10px] text-muted font-mono">
                            {msgs?.length || 0} msgs · {Number(llm.input_tokens || 0)} in / {Number(llm.output_tokens || 0)} out
                          </div>
                          {!routing && resp && !resp.toLowerCase().startsWith('tool_call:') && (
                            <div className="text-[11px] text-muted italic truncate mt-0.5">{resp}</div>
                          )}
                        </button>
                        {open && (
                          <div className="border-t border-line bg-page">
                            <div className="px-3 py-2 space-y-1 text-[11px] font-mono border-b border-line/60">
                              <div className="flex gap-2">
                                <span className="text-muted shrink-0 w-24">span_id</span>
                                <span className="text-ink break-all">{llm.id}</span>
                              </div>
                              {llm.error && (
                                <div className="flex gap-2">
                                  <span className="text-[#f75f6a] shrink-0 w-24">error</span>
                                  <span className="text-[#f75f6a] break-all">{llm.error}</span>
                                </div>
                              )}
                              <div className="flex gap-2">
                                <span className="text-muted shrink-0 w-24">response</span>
                                <span className="text-ink break-all">{llm.response || '—'}</span>
                              </div>
                            </div>
                            {msgs && (
                              <MessageRows messages={msgs} prevCount={prevMsgCount} />
                            )}
                            <div className="px-3 py-2 border-t border-line">
                              <button
                                type="button"
                                className="text-[11px] font-mono text-ink hover:text-muted"
                                onClick={() => onOpenLog?.(llm.id, { runKey, steps })}
                              >
                                Open full log…
                              </button>
                            </div>
                          </div>
                        )}

                        {block.tools.length > 0 && (
                          <div className="border-t border-line relative pl-3">
                            <div className="absolute left-[11px] top-0 bottom-0 w-px bg-line" aria-hidden />
                            {block.tools.map((tool) => {
                              const tmd = metadataObject(tool)
                              const tn = toolNameFromStep(tool)
                              const handoff = isHandoffToolStep(tool, routeAgents)
                              const goto = handoffGotoFromToolMetadata(tool)
                              if (handoff) {
                                const tgt = matchHandoffTargetToAgent(goto || '', agents)
                                const tgtIdx = tgt != null ? agents.indexOf(tgt) : -1
                                const tpal = tgtIdx >= 0 ? traceAgentStyle(tgtIdx) : pal
                                return (
                                  <div
                                    key={tool.id}
                                    ref={(el) => setRef(`tool-${tool.id}`, el)}
                                    className="relative pl-3 py-1.5"
                                  >
                                    <div
                                      className="flex items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] font-mono"
                                      style={{
                                        borderColor: tpal.border,
                                        background: tpal.dimBg,
                                        color: tpal.accent,
                                      }}
                                    >
                                      <span>→</span>
                                      <span className="font-medium">{tn}</span>
                                      <span className="opacity-70 ml-auto truncate">{goto ? `goto: ${goto}` : ''}</span>
                                    </div>
                                  </div>
                                )
                              }
                              const tOpen = openKey === `tool-${tool.id}`
                              const res = firstLine(tool.response || tmd.tool_result)
                              return (
                                <div key={tool.id} ref={(el) => setRef(`tool-${tool.id}`, el)} className="relative">
                                  <button
                                    type="button"
                                    className="w-full flex items-center gap-2 py-1.5 pl-3 pr-2 text-left hover:bg-inset/80"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      setOpenKey((k) => (k === `tool-${tool.id}` ? null : `tool-${tool.id}`))
                                      setTimeout(redrawArrows, 0)
                                    }}
                                  >
                                    <span
                                      className="w-1 h-1 rounded-sm shrink-0"
                                      style={{ background: pal.accent, opacity: 0.75 }}
                                    />
                                    <span className="flex-1 min-w-0 flex items-center gap-2 rounded-full border border-line bg-inset px-2 py-0.5">
                                      <span className="text-[11px] text-muted truncate">{tn}</span>
                                      {toolArgsShort(tool) && (
                                        <span className="text-[10px] text-muted truncate max-w-[7rem]">{toolArgsShort(tool)}</span>
                                      )}
                                    </span>
                                  </button>
                                  {res && (
                                    <div className="pl-8 pr-2 pb-1.5 text-[11px] font-mono text-ink truncate" title={res}>
                                      → {res.length > 48 ? `${res.slice(0, 47)}…` : res}
                                    </div>
                                  )}
                                  {tOpen && (
                                    <div className="border-t border-line bg-page px-3 py-2 text-[11px] font-mono space-y-1 mb-1">
                                      <div className="flex gap-2">
                                        <span className="text-muted w-20 shrink-0">span_id</span>
                                        <span className="break-all">{tool.id}</span>
                                      </div>
                                      <div className="flex gap-2">
                                        <span className="text-muted w-20 shrink-0">result</span>
                                        <span className="break-all text-ink">{String(tool.response || tmd.tool_result || '—')}</span>
                                      </div>
                                      <button
                                        type="button"
                                        className="text-ink hover:text-muted"
                                        onClick={() => onOpenLog?.(tool.id, { runKey, steps })}
                                      >
                                        Open full log…
                                      </button>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })
                  })()}
                </div>
              </div>
            )
          })}

        </div>
      </div>
    </div>
  )
}
