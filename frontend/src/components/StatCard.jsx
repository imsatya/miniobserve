export default function StatCard({ label, value, sub, color = 'accent', icon: Icon }) {
  const colors = {
    accent: 'text-accent',
    green: 'text-green',
    red: 'text-red',
    yellow: 'text-yellow',
  }
  return (
    <div className="bg-surface border border-line rounded-xl p-5 flex flex-col gap-2 shadow-sm">
      <div className="flex items-center justify-between">
        <span className="text-muted text-xs font-mono uppercase tracking-widest">{label}</span>
        {Icon && <Icon size={14} className={colors[color]} />}
      </div>
      <div className={`text-2xl font-mono font-semibold ${colors[color]}`}>{value}</div>
      {sub && <div className="text-muted text-xs font-mono">{sub}</div>}
    </div>
  )
}
