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

/**
 * One horizontal stacked bar (green / gray / orange) — same visual language as RunCacheBreakdown.
 */
export default function StepTokenMixBar({ cached, uncached, output, label = 'This step' }) {
  const bar_total = (Number(cached) || 0) + (Number(uncached) || 0) + (Number(output) || 0)
  const chartRows = [
    {
      label,
      cached: Number(cached) || 0,
      uncached: Number(uncached) || 0,
      output: Number(output) || 0,
      bar_total,
    },
  ]

  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted mb-2 flex flex-wrap gap-3 items-center">
        <span>Token mix</span>
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
      <ResponsiveContainer width="100%" height={56}>
        <BarChart
          data={chartRows}
          layout="vertical"
          margin={{ left: 4, right: 12, top: 4, bottom: 4 }}
          barCategoryGap={6}
        >
          <XAxis type="number" tick={{ fill: '#64748b', fontSize: 10 }} />
          <YAxis type="category" dataKey="label" width={100} tick={{ fill: '#64748b', fontSize: 9 }} />
          <Tooltip content={<CacheBarTooltip />} cursor={{ fill: 'rgba(15,23,42,0.04)' }} />
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
  )
}
