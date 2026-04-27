/** Parse `metadata` from API (string JSON or object). */
export function parseLogMetadata(raw) {
  if (raw == null) return {}
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw || '{}')
    } catch {
      return {}
    }
  }
  return {}
}

export function runIdFromLog(log) {
  const m = parseLogMetadata(log?.metadata)
  return m.run_id || m.runId || ''
}
