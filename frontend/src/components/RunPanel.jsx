import { useState, useEffect, useCallback, useRef, Fragment } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import {
  Copy, AlertCircle, Clock, Layers, RefreshCw, LayoutGrid,
} from 'lucide-react'
import { fetchRuns, fetchRunDetail, fetchRunReplay } from '../api.js'
import {
  stepPrimaryLabel, traceStepDisplayLabel, stepsWithDepth, spanTypeLabel, buildWaterfallRows,
  computeCacheBreakdownFromSteps,
  cognitiveModeColor,
  fingerprintSegmentsFromModeFractions,
  parseOpenAIToolCallsSummary, formatToolCallsSummaryShort,
  llmResponseSubtitleLine,
  isSessionEnvelopeSpan,
  metadataObject,
  stepCognitiveDotColor,
  aggregateDecisionForRun,
} from '../runUi.js'
import RunCacheBreakdown from './RunCacheBreakdown.jsx'
import RunCachingSummary from './RunCachingSummary.jsx'
import RunTraceV2Modal from './RunTraceV2Modal.jsx'
import { buildAgentTraceLayout } from '../traceLayout.js'
import { formatLocalTimestamp } from '../formatTime.js'

const RUNS_POLL_MS = 60_000

function formatLatencySeconds(sec) {
  const n = Number(sec)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) < 1e-12) return '0s'
  if (n < 0.01) return `${n.toFixed(4)}s`
  if (n < 1) return `${n.toFixed(3)}s`
  return `${n.toFixed(2)}s`
}

function formatLatencyMs(ms) {
  return formatLatencySeconds((Number(ms) || 0) / 1000)
}

function StatusDot({ error }) {
  return (
    <span className={`inline-block w-1.5 h-1.5 rounded-full ${error ? 'bg-[#f75f6a]' : 'bg-[#22d3a0]'}`} />
  )
}

/** Time-weighted cognitive phase mix (same palette as step dots / log chips). */
function CognitiveMixStrip({ segments, modeFractions }) {
  const raw =
    Array.isArray(segments) && segments.length
      ? segments.filter((s) => s && String(s.mode || '').trim() && Number(s.fraction) > 0)
      : fingerprintSegmentsFromModeFractions(modeFractions)
  if (!raw || !raw.length) {
    return (
      <div
        className="h-1 w-full max-w-[min(320px,100%)] min-w-[80px] bg-line rounded"
        title="No cognitive phase mix yet — ingest new logs or run backend/backfill_cognitive.py"
      />
    )
  }
  // Merge adjacent same-mode segments (per-span storage can produce duplicates)
  const merged = []
  for (const seg of raw) {
    const last = merged[merged.length - 1]
    if (last && last.mode === seg.mode) {
      last.fraction += Number(seg.fraction || 0)
    } else {
      merged.push({ mode: seg.mode, fraction: Number(seg.fraction || 0) })
    }
  }
  const sum = merged.reduce((a, s) => a + Number(s.fraction || 0), 0) || 1
  return (
    <div
      className="flex h-1 w-full max-w-[min(320px,100%)] min-w-[80px] rounded overflow-hidden"
      title="Cognitive phase mix (heuristic, time-weighted)"
    >
      {merged.map((seg, i) => (
        <div
          key={`${seg.mode}-${i}`}
          style={{
            flex: `${Math.max(Number(seg.fraction || 0) / sum, 0)} 1 0%`,
            minWidth: 1,
            background: cognitiveModeColor(seg.mode),
          }}
        />
      ))}
    </div>
  )
}

export default function RunPanel({ onOpenLog, runsRefreshNonce = 0 }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const hasLoadedRunsRef = useRef(false)
  const [selectedKey, setSelectedKey] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  /** `{ runKey }` — Trace multi-agent viz modal. */
  const [traceViz, setTraceViz] = useState(null)

  const loadRuns = useCallback(async ({ manual } = {}) => {
    const initial = !hasLoadedRunsRef.current
    if (initial) setLoading(true)
    if (manual) setRefreshing(true)
    try {
      const d = await fetchRuns({ scan_limit: 8000, runs_limit: 150 })
      setRuns(d.runs || [])
      hasLoadedRunsRef.current = true
    } catch {
      if (!hasLoadedRunsRef.current) setRuns([])
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    loadRuns()
  }, [loadRuns, runsRefreshNonce])

  useEffect(() => {
    const t = setInterval(() => loadRuns(), RUNS_POLL_MS)
    return () => clearInterval(t)
  }, [loadRuns])

  useEffect(() => {
    if (!selectedKey) return
    if (!runs.some((r) => r.run_key === selectedKey)) {
      setSelectedKey(null)
      setDetail(null)
    }
  }, [runs, selectedKey])

  const openRun = async (runKey) => {
    if (selectedKey === runKey) {
      setSelectedKey(null)
      setDetail(null)
      return
    }
    setSelectedKey(runKey)
    setDetailLoading(true)
    setDetail(null)
    try {
      const d = await fetchRunDetail(runKey)
      setDetail(d)
    } catch {
      setDetail(null)
    }
    setDetailLoading(false)
  }

  const copyReplay = async () => {
    if (!selectedKey) return
    try {
      const d = await fetchRunReplay(selectedKey)
      await navigator.clipboard.writeText(JSON.stringify(d, null, 2))
    } catch { /* ignore */ }
  }

  const stepsOrdered = detail?.steps ? stepsWithDepth(detail.steps) : []
  const sessionEnvelope = stepsOrdered.find(isSessionEnvelopeSpan) || null
  const activitySteps = sessionEnvelope
    ? stepsOrdered.filter((s) => !isSessionEnvelopeSpan(s))
    : stepsOrdered
  const executionTimelineSteps = activitySteps.filter((s) => {
    const md = metadataObject(s)
    return !(md?.decision && typeof md.decision === 'object')
  })
  const executionStepIdSet = new Set(executionTimelineSteps.map((s) => String(s.id)))
  const executionSiblingIds = executionTimelineSteps.map((s) => s.id)
  const resolvedAgentById = detail?.steps?.length
    ? buildAgentTraceLayout(detail.steps).resolvedAgent
    : null
  const wf = buildWaterfallRows(executionTimelineSteps)
  const wfSec = wf.map((r) => ({ ...r, latency_s: (Number(r.latency_ms) || 0) / 1000 }))
  const analysis = detail?.analysis
  const decisionObs = detail?.decision_observability || { decisions: [], integrity_alerts: [] }
  const runDecisionSummary = aggregateDecisionForRun(activitySteps)
  const cacheBreakdown = (() => {
    if (!detail?.steps?.length) return null
    const executionSteps = detail.steps.filter((s) => executionStepIdSet.has(String(s.id)))
    const fallback = computeCacheBreakdownFromSteps(executionSteps, { resolvedAgentById })
    if (!detail?.cache_breakdown?.rows?.length) return fallback

    const stepById = new Map(detail.steps.map((s) => [String(s.id), s]))
    const rows = detail.cache_breakdown.rows
      .filter((r) => executionStepIdSet.has(String(r.id)))
      .map((r) => {
      const step = stepById.get(String(r.id))
      if (!step) return r
      return {
        ...r,
        label: traceStepDisplayLabel(step, { resolvedAgentById }),
      }
    })
    const totals = rows.reduce((acc, r) => {
      const cached = Number(r.cached || 0)
      const uncached = Number(r.uncached || 0)
      const output = Number(r.output || 0)
      const prompt_tokens = Number(r.prompt_tokens || (cached + uncached))
      const cost_usd = Number(r.cost_usd || 0)
      acc.cached += cached
      acc.uncached += uncached
      acc.output += output
      acc.prompt_tokens += prompt_tokens
      acc.cost_usd += cost_usd
      return acc
    }, { cached: 0, uncached: 0, output: 0, prompt_tokens: 0, cost_usd: 0 })
    totals.cache_pct = totals.prompt_tokens > 0 ? Math.round((totals.cached / totals.prompt_tokens) * 10000) / 100 : null
    totals.has_cached_prompt_data = totals.cached > 0
    return { ...detail.cache_breakdown, rows, totals }
  })()
  const sessionWallMs =
    sessionEnvelope && Number(sessionEnvelope.latency_ms) > 0
      ? Number(sessionEnvelope.latency_ms)
      : 0
  const totalStepLat =
    sessionWallMs > 0
      ? sessionWallMs
      : activitySteps.reduce((acc, s) => acc + Number(s.latency_ms || 0), 0) || 1
  const maxStepLat = activitySteps.reduce((acc, s) => Math.max(acc, Number(s.latency_ms || 0)), 1)
  const totalRunCostUsd =
    detail?.steps?.reduce((acc, s) => acc + Number(s.cost_usd || 0), 0) ?? 0
  const sessionDisplayName = (() => {
    if (!sessionEnvelope) return ''
    const md = metadataObject(sessionEnvelope)
    let n = String(md.agent_span_name || '').trim()
    if (n.toLowerCase().startsWith('agent/')) n = n.slice(6)
    if (!n) return String(sessionEnvelope.span_name || 'session').trim() || 'session'
    return n
  })()
  return (
    <div className="flex flex-col gap-4">
      {traceViz && (
        <RunTraceV2Modal
          runKey={traceViz.runKey}
          onClose={() => setTraceViz(null)}
          onOpenLog={(id, opts) => { const rv = traceViz; setTraceViz(null); onOpenLog(id, { ...opts, returnTo: () => setTraceViz(rv) }) }}
        />
      )}
      <div className="bg-surface border border-line rounded-xl overflow-hidden w-full">
        <div className="px-4 py-2 border-b border-line text-xs font-mono text-muted flex justify-between items-center">
          <span>Runs</span>
          <div className="flex items-center gap-2">
            {loading && <span className="text-muted">loading…</span>}
            <button
              type="button"
              onClick={() => loadRuns({ manual: true })}
              disabled={refreshing}
              title="Refresh runs"
              className="p-1 rounded text-muted hover:text-ink hover:bg-inset disabled:opacity-40"
            >
              <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        <table className="w-full table-fixed text-xs font-mono">
          <thead>
            <tr className="text-muted border-b border-line">
              <th scope="col" className="py-2 px-2 font-normal text-center w-[6.25rem]">
                <span className="sr-only">Open multi-agent trace view</span>
              </th>
              <th scope="col" className="py-2 px-3 font-normal text-left">Phase</th>
              <th scope="col" className="py-2 px-3 font-normal text-left">Run key</th>
              <th scope="col" className="py-2 px-2 font-normal text-left w-[4.5rem]">Steps</th>
              <th scope="col" className="py-2 px-2 font-normal text-left w-[6.5rem]">Cost</th>
              <th scope="col" className="py-2 px-2 font-normal text-left w-[6.5rem]">Latency</th>
              <th scope="col" className="py-2 px-2 font-normal text-left w-[9rem]">Last (local)</th>
              <th scope="col" className="py-2 px-3 font-normal text-center w-[5.5rem]">Status</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-muted">
                  No runs yet. Send logs with X-MiniObserve-Run-Id or run_id.
                </td>
              </tr>
            )}
            {runs.map((r) => {
              const isSelected = selectedKey === r.run_key
              return (
                <Fragment key={r.run_key}>
                  {/* Run row */}
                  <tr
                    onClick={() => openRun(r.run_key)}
                    className={`border-b border-line/50 cursor-pointer hover:bg-inset transition-colors ${
                      isSelected ? 'bg-[#7c6af7]/10 border-[#7c6af7]/20' : ''
                    }`}
                    title="Click row to expand run detail"
                  >
                    <td className="px-2 py-3 text-center align-middle">
                      <button
                        type="button"
                        title="Open trace view — multi-agent columns, lanes, and handoffs"
                        aria-label="Open trace view for this run"
                        className="inline-flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-lg border border-[#7c6af7]/55 bg-[#7c6af7]/18 text-ink shadow-sm hover:bg-[#7c6af7]/28 hover:border-[#7c6af7]/75 hover:shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-[#7c6af7]/45 transition-colors"
                        onClick={(e) => {
                          e.stopPropagation()
                          setTraceViz({ runKey: r.run_key })
                        }}
                      >
                        <LayoutGrid size={16} className="text-[#7c6af7] shrink-0" aria-hidden />
                        <span className="text-[10px] font-semibold uppercase tracking-wide text-ink hidden min-[520px]:inline">Trace</span>
                      </button>
                    </td>
                    <td className="px-3 py-3 align-middle min-w-[10rem] max-w-[min(24rem,40vw)]">
                      <div className="min-w-0">
                        <CognitiveMixStrip segments={r.fingerprint_segments} modeFractions={r.mode_fractions} />
                      </div>
                    </td>
                    <td className="px-3 py-3 max-w-[min(48rem,68vw)] text-ink" title={r.run_key}>
                      <div className="min-w-0">
                        <div className="truncate">{r.run_key}</div>
                        {r.query_preview && (
                          <div className="text-[11px] text-muted font-sans leading-snug mt-0.5 line-clamp-2 break-words" title={r.query_preview}>
                            {r.query_preview}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-2 py-3 text-muted">{r.step_count}</td>
                    <td className="px-2 py-3 text-[#22d3a0]">${Number(r.total_cost_usd || 0).toFixed(4)}</td>
                    <td className="px-2 py-3 text-[#f7c948]">{formatLatencyMs(r.total_latency_ms)}</td>
                    <td className="px-2 py-3 text-muted whitespace-nowrap" title={r.ended_at || undefined}>
                      {formatLocalTimestamp(r.ended_at)}
                    </td>
                    <td className="px-3 py-3 text-center"><StatusDot error={r.has_error} /></td>
                  </tr>

                  {/* Inline detail panel */}
                  {isSelected && (
                    <tr className="border-b border-[#7c6af7]/20">
                      <td colSpan={8} className="bg-inset/70 px-5 py-4">
                        {detailLoading && (
                          <div className="text-ink text-xs font-mono py-2">Loading…</div>
                        )}

                        {!detailLoading && analysis && (
                          <div className="flex flex-col gap-4">
                            <div>
                              <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">Activity timeline</div>
                              <div className="rounded-lg border border-line bg-surface overflow-hidden">
                                {sessionEnvelope && (
                                  <div className="px-3 py-2 bg-inset/60 border-b border-line font-mono text-[11px] text-ink">
                                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                                      <span className="text-muted text-[9px] uppercase tracking-widest shrink-0">Session</span>
                                      <span className="font-medium text-ink">{sessionDisplayName}</span>
                                      <span className="text-[#f7c948] tabular-nums">{formatLatencyMs(sessionWallMs)}</span>
                                      <span className="text-[#22d3a0] tabular-nums">${totalRunCostUsd.toFixed(4)}</span>
                                      <span className="text-muted tabular-nums">{activitySteps.length} steps</span>
                                    </div>
                                    <div className="mt-2 h-px bg-line/80" aria-hidden />
                                  </div>
                                )}
                                <div className="overflow-hidden">
                                <div className="max-h-[min(60vh,560px)] overflow-y-auto">
                                {executionTimelineSteps.map((s, vi) => {
                                  const origIdx = stepsOrdered.findIndex((x) => x.id === s.id)
                                  const lat = Number(s.latency_ms || 0)
                                  const w = (lat / maxStepLat) * 100
                                  const stuck = !!s.cognitive_stuck
                                  const waiting = !!s.cognitive_waiting
                                  const flags = origIdx >= 0
                                    ? (analysis.step_flags || []).find((f) => f.index === origIdx)?.flags || []
                                    : []
                                  const pad = (s._depth || 0) * 10
                                  const tcSum = parseOpenAIToolCallsSummary(s.response)
                                  return (
                                    <div
                                      key={s.id}
                                      className={`flex items-stretch border-b border-line/40 cursor-pointer hover:bg-inset ${stuck ? 'bg-[#f75f6a]/8' : waiting ? 'bg-[#f59e0b]/8' : ''}`}
                                      onClick={() => onOpenLog(s.id, { runKey: selectedKey, steps: detail?.steps, siblings: executionSiblingIds })}
                                    >
                                      <div className="w-7 shrink-0 text-[10px] text-muted px-1 py-1.5 text-right">{vi + 1}</div>
                                      <div
                                        className="flex-1 min-w-0 py-1 pr-2 overflow-hidden"
                                        style={{ paddingLeft: 4 + pad }}
                                      >
                                        <div className="flex items-center gap-2 text-[11px] font-mono min-w-0">
                                          <span style={{ color: stepCognitiveDotColor(s) }} className="shrink-0">
                                            ●
                                          </span>
                                          <span
                                            className={`uppercase text-[9px] w-[5.75rem] shrink-0 truncate text-left font-semibold ${s.cognitive_mode ? '' : 'text-muted'}`}
                                            style={s.cognitive_mode ? { color: cognitiveModeColor(s.cognitive_mode) } : undefined}
                                            title="Heuristic label from span order / tools (not model intent)"
                                          >
                                            {s.cognitive_mode || '—'}
                                          </span>
                                          {spanTypeLabel(s) && (
                                            <span className="text-muted shrink-0">{spanTypeLabel(s)}</span>
                                          )}
                                          <span className="text-ink truncate min-w-0" title={stepPrimaryLabel(s)}>
                                            {traceStepDisplayLabel(s, { resolvedAgentById })}
                                          </span>
                                          {(flags.length > 0 || stuck || waiting) && (
                                            <span
                                              className={`shrink-0 ${stuck ? 'text-[#f75f6a]' : waiting ? 'text-[#f59e0b]' : 'text-[#f75f6a]'}`}
                                              title={[...flags, stuck && 'stuck', waiting && 'waiting'].filter(Boolean).join(', ')}
                                            >·</span>
                                          )}
                                        </div>
                                        {tcSum && (
                                          <div className="text-[10px] text-muted font-mono truncate mt-0.5 pr-1" title={tcSum.names.join(', ')}>
                                            {formatToolCallsSummaryShort(tcSum)}
                                          </div>
                                        )}
                                        {!tcSum && llmResponseSubtitleLine(s) ? (
                                          <div className="text-[10px] text-muted font-mono truncate mt-0.5 pr-1" title={llmResponseSubtitleLine(s)}>
                                            {llmResponseSubtitleLine(s)}
                                          </div>
                                        ) : !tcSum && s.span_type === 'tool' ? (() => {
                                          const res = String(metadataObject(s).tool_result ?? s.response ?? '')
                                          if (!res) return null
                                          return (
                                            <div className="text-[10px] text-muted font-mono truncate mt-0.5 pr-1" title={res}>
                                              → {res}
                                            </div>
                                          )
                                        })() : null}
                                        <div className="mt-1 h-1 rounded overflow-hidden bg-line flex">
                                          <div
                                            style={{
                                              width: `${Math.max(w, 0.4)}%`,
                                              background: stepCognitiveDotColor(s),
                                            }}
                                            className="h-full min-w-[2px]"
                                          />
                                        </div>
                                      </div>
                                      <div className="relative z-[1] shrink-0 bg-surface text-[10px] text-[#f7c948] px-1 py-1.5 pl-2 tabular-nums">
                                        {formatLatencyMs(lat)}
                                      </div>
                                      <div className="relative z-[1] shrink-0 bg-surface text-[10px] text-[#22d3a0] px-1 py-1.5 tabular-nums">
                                        ${Number(s.cost_usd || 0).toFixed(4)}
                                      </div>
                                    </div>
                                  )
                                })}
                                {!!runDecisionSummary && (
                                  <div className="px-3 py-2 border-y border-line/60 bg-inset/40">
                                    <span className="text-[9px] font-mono uppercase tracking-widest text-muted">Decision</span>
                                  </div>
                                )}
                                {!!runDecisionSummary && (
                                  <div
                                    className="flex items-stretch border-b border-line/40 cursor-pointer hover:bg-inset"
                                    onClick={() =>
                                      onOpenLog(runDecisionSummary.primaryStepId, {
                                        runKey: selectedKey,
                                        steps: detail?.steps,
                                        modalMode: 'decision-aggregate',
                                        decisionAggregate: runDecisionSummary,
                                      })
                                    }
                                  >
                                    <div className="w-7 shrink-0 text-[10px] text-muted px-1 py-1.5 text-right">•</div>
                                    <div className="flex-1 min-w-0 py-1 pr-2 overflow-hidden">
                                      <div className="flex items-center gap-2 text-[11px] font-mono min-w-0">
                                        <span className="inline-flex items-center rounded px-1.5 py-0.5 border text-[9px] uppercase tracking-wide text-emerald-600 border-emerald-500/35 bg-emerald-500/10">
                                          decision
                                        </span>
                                        <span className="text-ink truncate min-w-0">
                                          {runDecisionSummary.types.join(', ') || 'routing'}
                                        </span>
                                      </div>
                                      <div className="text-[10px] text-muted font-mono truncate mt-0.5 pr-1" title={`emitters: ${runDecisionSummary.emitters.join(', ')}`}>
                                        emitters: {runDecisionSummary.emitters.join(', ') || 'unknown'}
                                      </div>
                                    </div>
                                    <div className="relative z-[1] shrink-0 bg-surface text-[10px] text-[#f7c948] px-1 py-1.5 pl-2 tabular-nums">
                                      {runDecisionSummary.stepCount} span{runDecisionSummary.stepCount === 1 ? '' : 's'}
                                    </div>
                                    <div className="relative z-[1] shrink-0 bg-surface text-[10px] text-[#22d3a0] px-1 py-1.5 tabular-nums">
                                      open
                                    </div>
                                  </div>
                                )}
                                </div>
                                </div>
                              </div>
                            </div>

                            {cacheBreakdown && (
                              <div>
                                <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">Est Cost &amp; Prompt Caching</div>
                                <RunCacheBreakdown cacheBreakdown={cacheBreakdown} />
                                {cacheBreakdown.totals && (
                                  <RunCachingSummary totals={cacheBreakdown.totals} variant="inline" />
                                )}
                              </div>
                            )}

                            {!!decisionObs?.integrity_alerts?.length && (
                              <div className="rounded-lg bg-surface border border-line p-3">
                                <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">Integrity alerts</div>
                                <div className="flex flex-wrap gap-2">
                                  {decisionObs.integrity_alerts.map((a, idx) => (
                                    (() => {
                                      const stepPos = activitySteps.findIndex((s) => Number(s.id) === Number(a.step_id))
                                      const stepLabel = stepPos >= 0 ? `Step #${stepPos + 1}` : `Step ${a.step_id}`
                                      return (
                                    <button
                                      key={`${a.kind}-${a.step_id}-${idx}`}
                                      type="button"
                                      onClick={() => onOpenLog(a.step_id, { runKey: selectedKey, steps: detail?.steps })}
                                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[#f75f6a]/35 text-[#f75f6a] text-[10px] font-mono hover:bg-[#f75f6a]/10"
                                      title={a.message || a.kind}
                                    >
                                      {stepLabel}: {a.kind}{a.matching_mode ? ` (${a.matching_mode})` : ''}
                                    </button>
                                      )
                                    })()
                                  ))}
                                </div>
                              </div>
                            )}

                            <div className="rounded-lg bg-surface border border-line p-3">
                              <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2">Insights</div>
                              <p className="text-xs text-ink leading-relaxed">{analysis.summary_line}</p>
                              <div className="flex flex-wrap gap-2 mt-2">
                                {(analysis.badges || []).map((b) => (
                                  <span key={b.id} title={b.tooltip} className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-lineSoft text-[10px] text-muted cursor-help">
                                    {b.id === 'failed' && <AlertCircle size={10} />}
                                    {b.id === 'slow' && <Clock size={10} />}
                                    {b.id === 'tokens' && <Layers size={10} />}
                                    {b.label}
                                  </span>
                                ))}
                              </div>
                            </div>

                            {wfSec.length > 0 && (
                              <details className="rounded-lg border border-line bg-surface p-3 group">
                                <summary className="text-[10px] font-mono uppercase tracking-widest text-muted cursor-pointer list-none flex items-center gap-1 [&::-webkit-details-marker]:hidden">
                                  <span className="text-ink/70 group-open:rotate-90 transition-transform inline-block">▸</span>
                                  Latency waterfall (advanced)
                                </summary>
                                <div className="mt-3">
                                  <ResponsiveContainer width="100%" height={Math.min(520, 40 + wfSec.length * 28)}>
                                    <BarChart data={wfSec} layout="vertical" margin={{ left: 8, right: 8, top: 4, bottom: 4 }}>
                                      <XAxis type="number" tick={{ fill: '#9fb0cc', fontSize: 10 }} tickFormatter={formatLatencySeconds} />
                                      <YAxis type="category" dataKey="name" width={180} tick={{ fill: '#9fb0cc', fontSize: 9 }} />
                                      <Tooltip
                                        contentStyle={{ background: '#0f172a', border: '1px solid #27324a', fontSize: 11, color: '#e6edf7' }}
                                        formatter={(v) => [formatLatencySeconds(v), 'Latency']}
                                      />
                                      <Bar dataKey="latency_s" radius={[0, 2, 2, 0]}>
                                        {wfSec.map((_, i) => (
                                          <Cell
                                            key={i}
                                            fill={stepCognitiveDotColor(executionTimelineSteps[i])}
                                            fillOpacity={0.85}
                                          />
                                        ))}
                                      </Bar>
                                    </BarChart>
                                  </ResponsiveContainer>
                                </div>
                              </details>
                            )}

                            <div className="flex gap-2">
                              <button
                                type="button"
                                onClick={copyReplay}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-inset text-ink border border-line text-xs font-mono hover:bg-inset/80"
                              >
                                <Copy size={12} />
                                Copy replay JSON
                              </button>
                              <span className="text-muted text-[10px] font-mono self-center">Prompts + metadata per step (no secrets)</span>
                            </div>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
