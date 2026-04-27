import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { fetchRunDetail } from '../api.js'
import { stepsToGanttSegments } from '../runUi.js'
import RunTraceV2 from './RunTraceV2.jsx'

export default function RunTraceV2Modal({ runKey, onClose, onOpenLog }) {
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [detail, setDetail] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr(null)
    setDetail(null)
    fetchRunDetail(runKey)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch(() => {
        if (!cancelled) setErr('Could not load run')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [runKey])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { rangeMs, totalTokens, spanCount, anyError, steps } = useMemo(() => {
    if (!detail?.steps?.length) {
      return { rangeMs: 1, totalTokens: 0, spanCount: 0, anyError: false, steps: [] }
    }
    const { rangeMs: rm } = stepsToGanttSegments(detail.steps)
    let tok = 0
    let err = false
    for (const s of detail.steps) {
      tok += Number(s.input_tokens || 0) + Number(s.output_tokens || 0)
      if (s.error) err = true
    }
    return {
      rangeMs: Math.max(rm, 1),
      totalTokens: tok,
      spanCount: detail.steps.length,
      anyError: err,
      steps: detail.steps,
    }
  }, [detail])

  return (
    <div
      className="mo-modal-overlay z-[80] p-3 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="trace-pipeline-title"
      onClick={onClose}
    >
      <div
        className="mo-modal-panel flex h-[min(92vh,900px)] w-full max-w-[min(96vw,1400px)] flex-col overflow-hidden rounded-xl font-sans text-[13px]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex h-[52px] shrink-0 items-center border-b border-line bg-surface px-4 gap-3">
          <span id="trace-pipeline-title" className="text-muted text-[10px] font-mono uppercase tracking-widest shrink-0">
            Trace
          </span>
          <span className="font-mono text-xs text-ink truncate max-w-[min(50vw,420px)]" title={runKey}>
            {runKey}
          </span>
          <div className="ml-auto flex items-center gap-0 border border-line rounded-lg overflow-hidden font-mono text-[11px]">
            <div className="px-3 py-1.5 text-right border-r border-line bg-inset/30">
              <div className="font-medium text-ink">{(rangeMs / 1000).toFixed(2)}s</div>
              <div className="text-[9px] uppercase tracking-wide text-muted">wall</div>
            </div>
            <div className="px-3 py-1.5 text-right border-r border-line bg-inset/30">
              <div className="font-medium text-ink">{totalTokens.toLocaleString()}</div>
              <div className="text-[9px] uppercase tracking-wide text-muted">tokens</div>
            </div>
            <div className="px-3 py-1.5 text-right bg-inset/30">
              <div className="font-medium text-ink">{spanCount}</div>
              <div className="text-[9px] uppercase tracking-wide text-muted">spans</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="mo-modal-close shrink-0 rounded-lg p-2"
            title="Close"
          >
            <X size={18} />
          </button>
        </header>

        {loading && <div className="p-4 font-mono text-sm text-ink">Loading…</div>}
        {err && <div className="p-4 font-mono text-sm text-[#f75f6a]">{err}</div>}
        {!loading && !err && (
          <RunTraceV2
            steps={steps}
            runKey={runKey}
            onOpenLog={onOpenLog}
            rangeMs={rangeMs}
            totalTokens={totalTokens}
            spanCount={spanCount}
            anyError={anyError}
          />
        )}
      </div>
    </div>
  )
}
