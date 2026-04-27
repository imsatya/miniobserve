import { lcpSplit, PROMPT_TRUNCATION_HINT } from '../cacheBoundary.js'

function promptText(row) {
  const p = row?.prompt
  return typeof p === 'string' ? p : p == null ? '' : String(p)
}

export default function StepCacheBoundary({ hitStep, missStep }) {
  if (!hitStep || !missStep) return null

  const a = promptText(hitStep)
  const b = promptText(missStep)
  if (!a.length && !b.length) return null

  const { length, prefix, restHit, restMiss } = lcpSplit(a, b)
  const maxLen = Math.max(a.length, b.length)
  const pct = maxLen > 0 ? Math.round((length / maxLen) * 1000) / 10 : 0
  const likelyTruncated = a.length >= PROMPT_TRUNCATION_HINT || b.length >= PROMPT_TRUNCATION_HINT

  return (
    <div className="rounded-lg border border-line bg-[#7c6af7]/5 p-3 shrink-0">
      <div className="text-[10px] font-mono uppercase tracking-widest text-ink mb-2">Cache boundary</div>
      <p className="text-xs text-ink leading-relaxed mb-3">
        Longest common prefix between the <strong>last cache-hit</strong> step (#{hitStep.id}) and this{' '}
        <strong>first adjacent cache-miss</strong> step (#{missStep.id}). Prefix-based caches align up to the first
        differing character.
      </p>
      <div className="text-xs font-mono text-ink mb-2">
        <span className="text-muted">Shared prefix length:</span>{' '}
        <span className="text-ink font-semibold tabular-nums">{length.toLocaleString()}</span>
        <span className="text-muted"> chars</span>
        {maxLen > 0 && (
          <span className="text-muted"> ({pct}% of longer prompt)</span>
        )}
      </div>
      {likelyTruncated && (
        <p className="text-[10px] text-amber-800 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-1.5 mb-3">
          Prompts may be truncated at ingest (~{PROMPT_TRUNCATION_HINT} chars). Boundary is only as accurate as stored
          text.
        </p>
      )}
      <div className="space-y-2">
        <div>
          <div className="text-[10px] text-muted font-mono mb-1">After last hit (#{hitStep.id}) — divergent tail</div>
          <pre className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-all max-h-32 overflow-y-auto rounded border border-line bg-surface p-2 text-ink">
            <span className="text-slate-400">{prefix}</span>
            <span className="text-green-800 bg-green-500/15">{restHit}</span>
          </pre>
        </div>
        <div>
          <div className="text-[10px] text-muted font-mono mb-1">This miss (#{missStep.id}) — divergent tail</div>
          <pre className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-all max-h-32 overflow-y-auto rounded border border-line bg-surface p-2 text-ink">
            <span className="text-slate-400">{prefix}</span>
            <span className="text-amber-900 bg-amber-500/15">{restMiss}</span>
          </pre>
        </div>
      </div>
      <p className="text-[10px] text-muted mt-2 leading-relaxed">
        Gray = shared prefix; colored = only the part that differs from the other prompt after that prefix.
      </p>
    </div>
  )
}
