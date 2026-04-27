import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, LabelList,
} from 'recharts'
import { CACHE_BAR_COLORS as C } from '../cacheBarColors.js'

function pct(seg, total) {
  if (!total || !seg) return null
  return Math.round((seg / total) * 1000) / 10
}

function CacheBarTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  if (!row) return null
  const t = row.bar_total || 0
  return (
    <div className="bg-surface border border-line shadow-md rounded-lg p-3 text-xs font-mono text-ink max-w-xs">
      <div className="text-muted mb-2 truncate" title={row.label}>{row.label}</div>
      {[
        ['Cached prompt', row.cached, C.cached],
        ['Uncached prompt', row.uncached, C.uncached],
        ['Completion', row.output, C.output],
      ].map(([name, v, color]) => (
        <div key={name} className="flex justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ background: color }} />
            {name}
          </span>
          <span>
            {v?.toLocaleString?.() ?? v}
            {t > 0 && v != null ? ` (${pct(v, t)}%)` : ''}
          </span>
        </div>
      ))}
      <div className="border-t border-line mt-2 pt-2 text-muted">
        Total tokens (bar): {t.toLocaleString()}
      </div>
    </div>
  )
}

function SegmentPctLabel({ x, y, width, height, value, payload, fill }) {
  const t = (payload?.cached || 0) + (payload?.uncached || 0) + (payload?.output || 0)
  if (!t || !value || value < t * 0.07 || width < 28) return null
  const p = Math.round((value / t) * 1000) / 10
  const dark = fill === C.uncached || fill === C.output
  return (
    <text
      x={x + width / 2}
      y={y + height / 2}
      fill={dark ? '#0f172a' : '#ffffff'}
      fontSize={10}
      fontFamily="JetBrains Mono, monospace"
      textAnchor="middle"
      dominantBaseline="middle"
    >
      {p}%
    </text>
  )
}

export default function RunCacheBreakdown({ cacheBreakdown }) {
  if (!cacheBreakdown?.rows?.length) return null

  const { totals, rows } = cacheBreakdown
  const chartRows = rows.map((r) => ({
    ...r,
    label: (r.label || 'step').slice(0, 48) + ((r.label || '').length > 48 ? '…' : ''),
  }))

  const h = Math.min(520, 48 + chartRows.length * 36)

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-lg border border-line bg-inset p-4 flex flex-wrap items-center gap-6">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Est. run cost</div>
          <div className="text-2xl font-mono font-semibold text-[#1ab896]">
            ${Number(totals.cost_usd || 0).toFixed(4)}
          </div>
          <div className="text-[10px] text-muted mt-1">
            total across all steps
          </div>
        </div>
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-1">Cache hit avg</div>
          <div className="text-2xl font-mono font-semibold text-ink">
            {totals.cache_pct != null ? `${totals.cache_pct}%` : '—'}
          </div>
          <div className="text-[10px] text-muted mt-1">
            cached prompt ÷ prompt tokens (run)
          </div>
        </div>
        {!totals.has_cached_prompt_data && (
          <p className="text-xs text-muted max-w-md">
            No cached prompt tokens recorded yet. OpenAI reports <code className="text-ink">prompt_tokens_details.cached_tokens</code> and Anthropic reports <code className="text-ink">cache_read_input_tokens</code> in usage when prompt caching applies; ingest via raw API dump or the SDK <code className="text-ink">@observe</code> decorator.
          </p>
        )}
      </div>

      <div>
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2 flex flex-wrap gap-3 items-center">
          <span>Token mix by step</span>
          <span className="inline-flex items-center gap-1.5 font-normal normal-case">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: C.cached }} /> cached prompt
          </span>
          <span className="inline-flex items-center gap-1.5 font-normal normal-case">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: C.uncached }} /> uncached prompt
          </span>
          <span className="inline-flex items-center gap-1.5 font-normal normal-case">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: C.output }} /> completion
          </span>
        </div>
        <ResponsiveContainer width="100%" height={h}>
          <BarChart
            data={chartRows}
            layout="vertical"
            margin={{ left: 4, right: 12, top: 8, bottom: 8 }}
            barCategoryGap={6}
          >
            <XAxis type="number" tick={{ fill: '#9fb0cc', fontSize: 10 }} />
            <YAxis
              type="category"
              dataKey="label"
              width={118}
              tick={{ fill: '#9fb0cc', fontSize: 9 }}
            />
            <Tooltip content={<CacheBarTooltip />} cursor={{ fill: 'rgba(17,27,49,0.55)' }} />
            <Bar dataKey="cached" stackId="tok" fill={C.cached} name="Cached prompt" isAnimationActive={false}>
              <LabelList dataKey="cached" content={<SegmentPctLabel fill={C.cached} />} />
            </Bar>
            <Bar dataKey="uncached" stackId="tok" fill={C.uncached} name="Uncached prompt" isAnimationActive={false}>
              <LabelList dataKey="uncached" content={<SegmentPctLabel fill={C.uncached} />} />
            </Bar>
            <Bar dataKey="output" stackId="tok" fill={C.output} name="Completion" isAnimationActive={false}>
              <LabelList dataKey="output" content={<SegmentPctLabel fill={C.output} />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-muted border-b border-line bg-inset">
              <th className="text-left px-3 py-2 font-normal">Step</th>
              <th className="text-right px-3 py-2 font-normal">Cached</th>
              <th className="text-right px-3 py-2 font-normal">Uncached</th>
              <th className="text-right px-3 py-2 font-normal">Output</th>
              <th className="text-right px-3 py-2 font-normal">Cache %</th>
              <th className="text-right px-3 py-2 font-normal">Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-line/60 hover:bg-inset/70">
                <td className="px-3 py-2 text-ink max-w-[200px] truncate" title={r.label}>{r.label}</td>
                <td className="px-3 py-2 text-right text-[#1ab896]">{r.cached.toLocaleString()}</td>
                <td className="px-3 py-2 text-right text-muted">{r.uncached.toLocaleString()}</td>
                <td className="px-3 py-2 text-right text-amber-700">{r.output.toLocaleString()}</td>
                <td className="px-3 py-2 text-right text-muted">
                  {r.cache_pct != null ? `${r.cache_pct}%` : '—'}
                </td>
                <td className="px-3 py-2 text-right text-[#1ab896]">${Number(r.cost_usd || 0).toFixed(6)}</td>
              </tr>
            ))}
            <tr className="bg-inset font-medium border-t-2 border-line">
              <td className="px-3 py-2 text-ink">Total</td>
              <td className="px-3 py-2 text-right text-[#1ab896]">{totals.cached.toLocaleString()}</td>
              <td className="px-3 py-2 text-right text-muted">{totals.uncached.toLocaleString()}</td>
              <td className="px-3 py-2 text-right text-amber-700">{totals.output.toLocaleString()}</td>
              <td className="px-3 py-2 text-right text-ink">
                {totals.cache_pct != null ? `${totals.cache_pct}%` : '—'}
              </td>
              <td className="px-3 py-2 text-right text-[#1ab896]">${totals.cost_usd.toFixed(6)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
