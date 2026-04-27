/** Format API ISO timestamps in the browser's local timezone. */
export function formatLocalTimestamp(iso, options) {
  if (iso == null || iso === '') return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, options ?? { dateStyle: 'short', timeStyle: 'medium' })
}
