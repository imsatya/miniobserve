import RunPanel from './RunPanel.jsx'

/**
 * Trace tab: agent runs with cognitive timeline (devtools-style) and fingerprints.
 * Run list (full width) above run detail (full width) in RunPanel (single poll).
 */
export default function TracePanel({ onOpenLog, runsRefreshNonce = 0 }) {
  return (
    <div className="flex flex-col gap-3 view-transition-trace">
      <RunPanel onOpenLog={onOpenLog} runsRefreshNonce={runsRefreshNonce} />
    </div>
  )
}
