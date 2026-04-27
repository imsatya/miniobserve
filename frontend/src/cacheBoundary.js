/**
 * Adjacent cache transition: previous step had cached prompt tokens, current did not.
 * Matches prefix-cache intuition: compare prompts at this boundary with LCP.
 */

/** Ingest truncates prompts in main.py to this length (approximate UI disclaimer). */
export const PROMPT_TRUNCATION_HINT = 4000

export function lcpLength(a, b) {
  const s = typeof a === 'string' ? a : String(a ?? '')
  const t = typeof b === 'string' ? b : String(b ?? '')
  let i = 0
  const n = Math.min(s.length, t.length)
  while (i < n && s[i] === t[i]) i++
  return i
}

export function lcpSplit(a, b) {
  const s = typeof a === 'string' ? a : String(a ?? '')
  const t = typeof b === 'string' ? b : String(b ?? '')
  const length = lcpLength(s, t)
  return {
    length,
    prefix: s.slice(0, length),
    restHit: s.slice(length),
    restMiss: t.slice(length),
  }
}

function cachedPromptTokens(step) {
  const inp = Number(step?.input_tokens) || 0
  let c = Number(step?.cached_input_tokens) || 0
  return Math.max(0, Math.min(c, inp))
}

/** Chronological order (same idea as runUi stepsWithDepth sort). */
export function orderStepsForRun(steps) {
  if (!steps?.length) return []
  return [...steps].sort((a, b) => {
    const ta = String(a.timestamp || '')
    const tb = String(b.timestamp || '')
    if (ta !== tb) return ta.localeCompare(tb)
    return Number(a.id) - Number(b.id)
  })
}

/**
 * If `currentId` is the first step after a cache hit where this step misses, return { hit, miss }.
 * "Miss" = cached prompt tokens === 0 while input_tokens > 0; "hit" = previous step had cached > 0.
 */
export function findAdjacentHitToMissPair(steps, currentId) {
  const ordered = orderStepsForRun(steps)
  const idx = ordered.findIndex((s) => Number(s.id) === Number(currentId))
  if (idx <= 0) return null
  const prev = ordered[idx - 1]
  const cur = ordered[idx]
  if (Number(cur.id) !== Number(currentId)) return null
  const inp = Number(cur.input_tokens) || 0
  if (inp <= 0) return null
  if (cachedPromptTokens(prev) <= 0) return null
  if (cachedPromptTokens(cur) > 0) return null
  return { hit: prev, miss: cur }
}
