import { X, Copy, ChevronDown, ChevronLeft, ChevronRight, Maximize2, Minimize2 } from 'lucide-react'
import { useState, useEffect, useRef, useCallback } from 'react'
import { parseLogMetadata } from '../metadata.js'
import {
  parseOpenAIToolCallsSummary,
  isSessionEnvelopeSpan,
  cognitiveModeChipStyle,
} from '../runUi.js'
import StepTokenMixBar from './StepTokenMixBar.jsx'
import StepCacheBoundary from './StepCacheBoundary.jsx'
import { findAdjacentHitToMissPair } from '../cacheBoundary.js'
import { formatLocalTimestamp } from '../formatTime.js'

/** @returns {object|array|null} */
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

/** Python Tracer stores step + fingerprint JSON in ``prompt``; chat lives in ``request.messages`` / ``messages``. */
function isTracerStepSummaryPrompt(text) {
  const o = tryParseJson(text)
  return !!(
    o &&
    typeof o === 'object' &&
    !Array.isArray(o) &&
    'step' in o &&
    'fingerprint' in o &&
    'had_tool_call' in o
  )
}

/** API may return ``messages`` as JSON string (SQLite / older paths). */
function normalizedChatMessages(log) {
  const raw = log?.messages
  if (Array.isArray(raw) && raw.length) return raw
  if (typeof raw !== 'string' || !raw.trim()) return null
  try {
    const p = JSON.parse(raw)
    return Array.isArray(p) && p.length ? p : null
  } catch {
    return null
  }
}

/** Per-step token split for OpenAI-style prompt cache (same rules as run_utils / runUi). */
function stepPromptCacheStats(log) {
  const inp = Number(log.input_tokens) || 0
  let cached = Number(log.cached_input_tokens) || 0
  if (cached === 0 && log.metadata && typeof log.metadata === 'object') {
    cached = Number(log.metadata.cache_read_tokens || log.metadata.cache_read) || 0
  }
  cached = Math.max(0, Math.min(cached, inp))
  const uncached = Math.max(0, inp - cached)
  const out = Number(log.output_tokens) || 0
  return { inp, cached, uncached, out, hasCached: cached > 0, hasAnyTokens: inp > 0 || out > 0 }
}

function StepPromptCaching({ log }) {
  const { inp, cached, uncached, out, hasCached, hasAnyTokens } = stepPromptCacheStats(log)
  const barTotal = cached + uncached + out

  return (
    <div className="rounded-lg border border-line bg-inset p-3 shrink-0">
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-3">Prompt caching</div>

      {!hasAnyTokens && (
        <p className="text-xs text-muted font-mono">No token counts on this step.</p>
      )}

      {hasAnyTokens && barTotal > 0 && (
        <>
          <StepTokenMixBar cached={cached} uncached={uncached} output={out} label="This step" />
          {inp > 0 && !hasCached && (
            <p className="text-[10px] text-muted leading-relaxed border-t border-line/60 mt-3 pt-3">
              No cached prompt tokens on this request — context may not be cache-eligible yet, or ingest missing{' '}
              <code className="text-ink">cached_input_tokens</code> / completion{' '}
              <code className="text-ink">usage</code>.
            </p>
          )}
        </>
      )}

      {hasAnyTokens && barTotal === 0 && (
        <p className="text-xs text-muted font-mono">Token fields are zero — nothing to show in the bar.</p>
      )}
    </div>
  )
}

const ROLE_LABEL_COLOR = {
  system:    'text-ink',
  user:      'text-emerald-600',
  assistant: 'text-sky-500',
}
const ROLE_STRIPE = {
  system:    'border-l-2 border-[#7c6af7]/50 pl-3',
  user:      '',
  assistant: 'border-l-2 border-sky-400/50 pl-3',
}

function MessagesSection({ messages, onCopy, copied, heading = 'Messages' }) {
  const [wrap, setWrap] = useState(true)
  if (!messages?.length) return null
  const allText = messages.map((m) => `[${m.role}]\n${m.content}`).join('\n\n')
  return (
    <div className="flex flex-col min-h-0 min-w-0 border border-line rounded-xl bg-inset overflow-hidden">
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-line bg-surface/95 backdrop-blur-sm">
        <span className="text-muted text-xs font-mono uppercase tracking-widest">{heading}</span>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setWrap((w) => !w)}
            className="text-muted hover:text-ink text-xs font-mono px-2 py-1 rounded border border-line hover:border-lineSoft transition-colors focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/50"
          >
            {wrap ? 'no wrap' : 'wrap'}
          </button>
          <button
            type="button"
            onClick={() => onCopy(allText)}
            className="text-muted hover:text-ink transition-colors flex items-center gap-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/50 rounded px-1"
          >
            <Copy size={12} />
            {copied ? 'copied!' : 'copy'}
          </button>
        </div>
      </div>
      <div className="overflow-auto max-h-[min(60vh,720px)] min-h-[8rem] p-4 flex flex-col gap-3">
        {messages.map((m, i) => {
          const labelColor = ROLE_LABEL_COLOR[m.role] || 'text-muted'
          const stripe = ROLE_STRIPE[m.role] || ''
          return (
            <div key={i} className={stripe}>
              <div className={`text-[10px] font-mono uppercase tracking-widest mb-1 ${labelColor}`}>
                {m.role}
              </div>
              <div className={`text-sm text-ink leading-relaxed font-sans ${wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre overflow-x-auto'}`}>
                {m.content}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function LogBodySection({ label, text, onCopyDisplayed, copied }) {
  const [raw, setRaw] = useState(false)
  const [wrap, setWrap] = useState(true)

  if (!text) return null

  const parsed = tryParseJson(text)
  const isJson = parsed !== null
  const displayText = isJson && !raw ? JSON.stringify(parsed, null, 2) : text
  const useSans = !isJson

  return (
    <div className="flex flex-col min-h-0 min-w-0 border border-line rounded-xl bg-inset overflow-hidden">
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-line bg-surface/95 backdrop-blur-sm">
        <span className="text-muted text-xs font-mono uppercase tracking-widest">{label}</span>
        <div className="flex flex-wrap items-center gap-2">
          {isJson && (
            <button
              type="button"
              onClick={() => setRaw((r) => !r)}
              className="text-muted hover:text-ink text-xs font-mono px-2 py-1 rounded border border-line hover:border-lineSoft transition-colors focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/50"
            >
              {raw ? 'formatted' : 'raw'}
            </button>
          )}
          <button
            type="button"
            onClick={() => setWrap((w) => !w)}
            className="text-muted hover:text-ink text-xs font-mono px-2 py-1 rounded border border-line hover:border-lineSoft transition-colors focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/50"
          >
            {wrap ? 'no wrap' : 'wrap'}
          </button>
          <button
            type="button"
            onClick={() => onCopyDisplayed(displayText)}
            className="text-muted hover:text-ink transition-colors flex items-center gap-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/50 rounded px-1"
          >
            <Copy size={12} />
            {copied ? 'copied!' : 'copy'}
          </button>
        </div>
      </div>
      <div
        className={`overflow-auto max-h-[min(60vh,720px)] min-h-[8rem] p-4 ${
          wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre overflow-x-auto'
        } ${useSans ? 'text-sm text-ink leading-relaxed font-sans' : 'text-sm text-ink font-mono leading-normal'}`}
      >
        {displayText}
      </div>
    </div>
  )
}

export default function LogModal({ log, runContext, onClose, onPrev, onNext }) {
  const [copied, setCopied] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const panelRef = useRef(null)
  const closeBtnRef = useRef(null)

  const copy = useCallback((text, key) => {
    navigator.clipboard.writeText(text)
    setCopied(key)
    setTimeout(() => setCopied(null), 1500)
  }, [])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { onClose(); return }
      if (e.key === 'ArrowLeft'  && onPrev) { e.preventDefault(); onPrev(); return }
      if (e.key === 'ArrowRight' && onNext) { e.preventDefault(); onNext(); return }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, onPrev, onNext])

  useEffect(() => {
    closeBtnRef.current?.focus()
  }, [log?.id])

  useEffect(() => {
    const el = panelRef.current
    if (!el) return
    const focusables = el.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const onTrap = (e) => {
      if (e.key !== 'Tab' || !focusables.length) return
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault()
          last?.focus()
        }
      } else if (document.activeElement === last) {
        e.preventDefault()
        first?.focus()
      }
    }
    el.addEventListener('keydown', onTrap)
    return () => el.removeEventListener('keydown', onTrap)
  }, [log?.id])

  if (!log) return null

  const metadata = parseLogMetadata(log.metadata)
  // Pull system_prompt_preview out of metadata so it's shown in the Prompt box, not buried below.
  const systemPromptPreview = metadata.system_prompt_preview || null
  const tracerStepSummary =
    typeof log.prompt === 'string' && isTracerStepSummaryPrompt(log.prompt)
  const metaKeys = Object.keys(metadata).filter(k => k !== 'system_prompt_preview')
  /** API may return `response` as string or parsed JSON (array / object). */
  const responseText =
    log.response == null || log.response === ''
      ? ''
      : typeof log.response === 'string'
        ? log.response
        : (() => {
            try {
              return JSON.stringify(log.response, null, 2)
            } catch {
              return String(log.response)
            }
          })()
  const toolCallsSummary = parseOpenAIToolCallsSummary(log.response)

  const cacheBoundaryPair =
    runContext?.steps?.length && log?.id != null
      ? findAdjacentHitToMissPair(runContext.steps, log.id)
      : null

  const metricItems = [
    { label: 'Model', value: log.model },
    { label: 'Latency', value: `${log.latency_ms?.toFixed(0)}ms` },
    { label: 'Cost', value: `$${log.cost_usd?.toFixed(6)}` },
    { label: 'Input tokens', value: log.input_tokens?.toLocaleString() },
    { label: 'Output tokens', value: log.output_tokens?.toLocaleString() },
    { label: 'Total tokens', value: log.total_tokens?.toLocaleString() },
    ...(log.run_id ? [{ label: 'run_id', value: log.run_id }] : []),
    ...(log.span_name ? [{ label: 'span_name', value: log.span_name }] : []),
    ...(log.span_type ? [{ label: 'span_type', value: log.span_type }] : []),
    ...(log.cognitive_mode && !isSessionEnvelopeSpan(log) ? [{ label: 'cognitive_phase', value: log.cognitive_mode }] : []),
    ...(log.cognitive_stuck && !isSessionEnvelopeSpan(log) ? [{ label: 'cognitive_stuck', value: 'true' }] : []),
    ...(log.cognitive_waiting && !isSessionEnvelopeSpan(log) ? [{ label: 'cognitive_waiting', value: 'true' }] : []),
    ...(log.parent_span_id != null ? [{ label: 'parent_span_id', value: String(log.parent_span_id) }] : []),
  ]

  const Badge = ({ children, color, className: cx = '' }) => {
    const colors = {
      green:     'bg-green/10 text-green border-green/25',
      red:       'bg-red/10 text-red border-red/25',
      accent:    'bg-accent/10 text-ink border-accent/25',
      sky:       'bg-sky-500/10 text-sky-500 border-sky-500/25',
      amber:     'bg-amber-500/10 text-amber-500 border-amber-500/25',
      violet:    'bg-violet-500/10 text-violet-500 border-violet-500/25',
      emerald:   'bg-emerald-500/10 text-emerald-600 border-emerald-500/25',
      muted:     'bg-inset text-muted border-line',
    }
    return (
      <span className={`text-xs font-mono px-2 py-0.5 rounded border ${colors[color] || colors.accent} ${cx}`}>
        {children}
      </span>
    )
  }

  // span_type may be a top-level column (newer logs) or nested in metadata (older logs ingested before column existed)
  const spanType = log.span_type || metadata.span_type || metadata.agent_span_type || null
  const spanTypeNorm = spanType ? String(spanType).toLowerCase() : ''
  /** Match Trace dashboard: agent violet, LLM emerald, tool amber. */
  const spanTypeBadgeColor = { llm: 'emerald', tool: 'amber', agent: 'violet' }

  /** h-[90vh] (not only max-h) so flex-1 + min-h-0 below actually yields a scrollable body; max-h alone lets content grow unbounded. */
  const shellClass = expanded
    ? 'fixed inset-3 sm:inset-4 md:inset-6 z-50 max-w-none max-h-none h-[calc(100vh-1.5rem)] sm:h-[calc(100vh-2rem)] md:h-[calc(100vh-3rem)]'
    : 'w-full max-w-[min(96vw,1200px)] h-[min(90vh,900px)] max-h-[90vh]'

  return (
    <div
      className="mo-modal-overlay z-50 p-3 sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="log-modal-title"
        className={`mo-modal-panel rounded-2xl flex flex-col min-h-0 overflow-hidden ${shellClass}`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 p-4 sm:p-5 border-b border-line shrink-0">
          <div className="flex items-center gap-3 flex-wrap min-w-0">
            <span id="log-modal-title" className="font-mono text-sm text-ink">
              #{log.id}
            </span>
            <Badge color={log.error ? 'red' : 'green'}>{log.error ? 'error' : 'ok'}</Badge>
            {log.provider && !['tool', 'agent'].includes(String(log.provider).toLowerCase()) && (
              <Badge color="accent">{log.provider}</Badge>
            )}
            {metadata.run_id && (
              <Badge color="accent" title="from X-MiniObserve-Run-Id">
                run_id: {String(metadata.run_id).slice(0, 36)}
                {String(metadata.run_id).length > 36 ? '…' : ''}
              </Badge>
            )}
            {spanType && (
              <Badge color={spanTypeBadgeColor[spanTypeNorm] || 'muted'}>{spanType}</Badge>
            )}
            {log.cognitive_mode && !isSessionEnvelopeSpan(log) && (
              <span
                className="text-xs font-mono px-2 py-0.5 rounded border whitespace-nowrap shrink-0"
                style={cognitiveModeChipStyle(log.cognitive_mode)}
                title="Heuristic cognitive phase (ingest)"
              >
                {log.cognitive_mode}
              </span>
            )}
            {!!log.cognitive_stuck && !isSessionEnvelopeSpan(log) && (
              <span
                className="text-xs font-mono px-2 py-0.5 rounded border whitespace-nowrap shrink-0"
                style={cognitiveModeChipStyle('stuck')}
                title="Repeat tool fingerprint"
              >
                stuck
              </span>
            )}
            {!!log.cognitive_waiting && !isSessionEnvelopeSpan(log) && !log.cognitive_stuck && (
              <span
                className="text-xs font-mono px-2 py-0.5 rounded border whitespace-nowrap shrink-0"
                style={cognitiveModeChipStyle('waiting')}
                title="Slow vs median for this tool in the run"
              >
                waiting
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {(onPrev || onNext) && (
              <div className="flex items-center border border-line rounded-lg overflow-hidden mr-1">
                <button
                  type="button"
                  onClick={onPrev}
                  disabled={!onPrev}
                  className="mo-modal-close p-1.5 disabled:opacity-30 disabled:cursor-default focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/40 border-r border-line"
                  title="Previous (←)"
                  aria-label="Previous step"
                >
                  <ChevronLeft size={15} />
                </button>
                <button
                  type="button"
                  onClick={onNext}
                  disabled={!onNext}
                  className="mo-modal-close p-1.5 disabled:opacity-30 disabled:cursor-default focus:outline-none focus:ring-1 focus:ring-[#7c6af7]/40"
                  title="Next (→)"
                  aria-label="Next step"
                >
                  <ChevronRight size={15} />
                </button>
              </div>
            )}
            <button
              ref={closeBtnRef}
              type="button"
              onClick={() => setExpanded((e) => !e)}
              className="mo-modal-close p-2 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#7c6af7]/40"
              title={expanded ? 'Exit fullscreen' : 'Expand'}
              aria-label={expanded ? 'Exit fullscreen' : 'Expand dialog'}
            >
              {expanded ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="mo-modal-close p-2 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#7c6af7]/40"
              aria-label="Close"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <div className="p-4 sm:p-5 flex flex-col gap-4 overflow-y-auto overflow-x-hidden flex-1 min-h-0 overscroll-contain">
          {/* ── Prompt + Response first — most important content ── */}
          <div className="flex flex-col lg:grid lg:grid-cols-2 lg:gap-4 lg:items-stretch gap-4 min-h-0 shrink-0">
            {(() => {
              // Priority: structured messages > synthesise from system_prompt_preview + plain prompt
              // (do not treat Tracer step-summary JSON in ``prompt`` as chat — use messages / metadata only.)
              const fromApi = normalizedChatMessages(log)
              const msgs = fromApi
                ? fromApi
                : systemPromptPreview
                  ? [
                      { role: 'system', content: systemPromptPreview },
                      ...(log.prompt && !tracerStepSummary ? [{ role: 'user', content: log.prompt }] : []),
                    ]
                  : null
              return msgs ? (
                <MessagesSection
                  messages={msgs}
                  onCopy={(t) => copy(t, 'prompt')}
                  copied={copied === 'prompt'}
                />
              ) : (
                <LogBodySection
                  label={tracerStepSummary ? 'Step metadata' : 'Prompt'}
                  text={log.prompt}
                  onCopyDisplayed={(t) => copy(t, 'prompt')}
                  copied={copied === 'prompt'}
                />
              )
            })()}
            <LogBodySection
              label="Response"
              text={responseText}
              onCopyDisplayed={(t) => copy(t, 'response')}
              copied={copied === 'response'}
            />
          </div>

          {log.error && (
            <div>
              <div className="text-muted text-xs font-mono uppercase tracking-widest mb-2">Error</div>
              <pre className="bg-[#f75f6a]/5 border border-[#f75f6a]/20 rounded-lg p-4 text-[#f75f6a] text-sm font-mono whitespace-pre-wrap overflow-x-auto max-h-48 leading-relaxed">
                {log.error}
              </pre>
            </div>
          )}

          {/* ── Tool calls ── */}
          {toolCallsSummary && (
            <div className="border border-[#7c6af7]/30 rounded-xl bg-[#7c6af7]/5 overflow-hidden shrink-0">
              <div className="px-4 py-3 border-b border-line bg-surface/80">
                <div className="text-ink text-xs font-mono uppercase tracking-widest">Tool calls</div>
              </div>
              <div className="px-4 py-3 flex flex-wrap gap-2">
                {toolCallsSummary.names.map((name, i) => (
                  <span
                    key={`${name}-${i}`}
                    className="text-xs font-mono px-2.5 py-1 rounded-md border border-line bg-surface text-ink"
                    title={`Call ${i + 1}`}
                  >
                    {name}
                  </span>
                ))}
              </div>
            </div>
          )}

          <StepPromptCaching log={log} />

          {cacheBoundaryPair && (
            <StepCacheBoundary hitStep={cacheBoundaryPair.hit} missStep={cacheBoundaryPair.miss} />
          )}

          {/* ── Collapsible metrics ── */}
          <details className="group border border-line rounded-xl bg-inset/50 min-w-0 w-full">
            <summary className="flex items-center gap-2 cursor-pointer list-none px-4 py-3 text-muted text-xs font-mono uppercase tracking-widest hover:bg-inset [&::-webkit-details-marker]:hidden">
              <ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" />
              Metrics
            </summary>
            <div className="px-4 pb-4 min-w-0 overflow-x-auto">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 min-w-0">
                {metricItems.map(({ label, value }) => (
                  <div key={label} className="bg-inset rounded-lg p-3 border border-line/80 min-w-0">
                    <div className="text-muted text-xs font-mono mb-1">{label}</div>
                    <div className="font-mono text-sm text-ink break-words [overflow-wrap:anywhere]">{value}</div>
                  </div>
                ))}
              </div>
            </div>
          </details>

          <div className="text-muted font-mono text-xs" title={log.timestamp ? `Stored: ${log.timestamp}` : undefined}>
            {formatLocalTimestamp(log.timestamp)}
          </div>

          {/* ── Collapsible metadata ── */}
          <details className="group border border-line rounded-xl bg-inset/50 min-w-0 w-full">
            <summary className="flex items-center gap-2 cursor-pointer list-none px-4 py-3 text-muted text-xs font-mono uppercase tracking-widest hover:bg-inset [&::-webkit-details-marker]:hidden">
              <ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" />
              Metadata
            </summary>
            <div className="px-4 pb-4 min-w-0 overflow-x-auto">
              <p className="text-muted text-xs mb-3">
                Set on ingest via headers like <code className="text-ink">X-MiniObserve-Run-Id</code>,{' '}
                <code className="text-ink">X-MiniObserve-Iteration</code>, etc.
              </p>
              {metaKeys.length === 0 ? (
                <div className="text-muted text-sm font-mono bg-inset border border-line rounded-lg p-3">
                  No metadata on this log.
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 min-w-0">
                  {metaKeys.map((k) => (
                    <div key={k} className="bg-inset rounded-lg p-3 border border-line min-w-0">
                      <div className="text-muted text-xs font-mono mb-1">{k}</div>
                      <div className="font-mono text-sm text-ink break-words [overflow-wrap:anywhere]">{String(metadata[k])}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </details>
        </div>
      </div>
    </div>
  )
}
