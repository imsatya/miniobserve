/**
 * Run-level prompt-cache key numbers (OpenAI-style cached_input_tokens split).
 *
 * @param {{ variant?: 'card' | 'inline' }} props — `inline`: under Prompt cache & tokens, large figures (same scale as Cache hit avg).
 */
export default function RunCachingSummary({ totals, variant = 'card' }) {
  if (!totals) return null

  const {
    cached = 0,
    uncached = 0,
    output = 0,
    prompt_tokens: promptTokens = 0,
    cache_pct: cachePct,
    has_cached_prompt_data: hasCached,
    cost_usd: costUsd = 0,
  } = totals

  const hasTokenData = promptTokens > 0 || output > 0
  const hasCost = Number(costUsd) > 0
  const inline = variant === 'inline'
  const big = 'text-2xl font-mono font-semibold text-ink tabular-nums'

  const shell = inline
    ? 'mt-5 pt-5 border-t border-line/70'
    : 'rounded-lg border border-line bg-inset p-3'

  // Always show cost card if we have cost data, even without token breakdown
  if (!hasTokenData && !hasCost) {
    return (
      <div className={shell}>
        {!inline && (
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-3">Prompt caching</div>
        )}
        <p className="text-xs text-muted font-mono mb-2">No token usage recorded for this run.</p>
      </div>
    )
  }

  return (
    <div className={shell}>
      {!inline && (
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-3">Prompt caching</div>
      )}

      <div className={`grid gap-3 ${inline ? 'mb-4' : 'gap-2 mb-3'} ${hasTokenData ? 'grid-cols-2 sm:grid-cols-5' : 'grid-cols-1 sm:grid-cols-2'}`}>
        {hasTokenData && (
          <>
            <div className={`rounded-md border border-line/80 bg-surface px-2 py-2 min-w-0 ${inline ? 'py-3' : ''}`}>
              <div className="text-[10px] text-muted font-mono mb-1">Cached prompt</div>
              <div className={inline ? big : 'text-sm font-mono text-[#1ab896] tabular-nums'}>{cached.toLocaleString()}</div>
            </div>
            <div className={`rounded-md border border-line/80 bg-surface px-2 py-2 min-w-0 ${inline ? 'py-3' : ''}`}>
              <div className="text-[10px] text-muted font-mono mb-1">Uncached prompt</div>
              <div className={inline ? big : 'text-sm font-mono text-muted tabular-nums'}>{uncached.toLocaleString()}</div>
            </div>
            <div className={`rounded-md border border-line/80 bg-surface px-2 py-2 min-w-0 ${inline ? 'py-3' : ''}`}>
              <div className="text-[10px] text-muted font-mono mb-1">Completion</div>
              <div className={inline ? big : 'text-sm font-mono text-amber-800 tabular-nums'}>{output.toLocaleString()}</div>
            </div>
            <div className={`rounded-md border border-line/80 bg-surface px-2 py-2 min-w-0 ${inline ? 'py-3' : ''}`}>
              <div className="text-[10px] text-muted font-mono mb-1">Cache hit (prompt)</div>
              <div className={inline ? big : 'text-sm font-mono text-ink tabular-nums'}>
                {cachePct != null ? `${cachePct}%` : '—'}
              </div>
            </div>
          </>
        )}
        <div className={`rounded-md border border-line/80 bg-surface px-2 py-2 min-w-0 ${inline ? 'py-3' : ''}`}>
          <div className="text-[10px] text-muted font-mono mb-1">Est. run cost</div>
          <div className={inline ? `${big} text-[#1ab896]` : 'text-sm font-mono text-[#1ab896] tabular-nums'}>
            ${Number(costUsd || 0).toFixed(4)}
          </div>
        </div>
      </div>

      {hasTokenData && (
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[10px] font-mono text-muted">
          <span>
            Prompt total: <span className={`${inline ? 'text-sm font-semibold text-ink' : 'text-ink'}`}>{promptTokens.toLocaleString()}</span>
          </span>
        </div>
      )}

      {hasTokenData && !hasCached && (
        <p className="text-xs text-muted mt-3 leading-relaxed border-t border-line/60 pt-3">
          No <strong>cached</strong> prompt tokens were reported. Prompt caching is reported by OpenAI via{' '}
          <code className="text-ink">prompt_tokens_details.cached_tokens</code> and by Anthropic via{' '}
          <code className="text-ink">cache_read_input_tokens</code>. Ingest via raw API dump or the SDK{' '}
          <code className="text-ink">@observe</code> decorator so the split appears here and in the chart above.
        </p>
      )}
    </div>
  )
}
