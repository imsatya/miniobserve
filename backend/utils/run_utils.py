"""Run grouping and lightweight analysis from stored logs."""
import json
from typing import Any, Dict, List, Optional

from cognitive.modes import infer_is_tool_span, is_session_envelope_row


def _parse_metadata(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}
    return {}


def effective_run_key(row: dict) -> str:
    """Stable key: run_id column, then run_id from metadata, else orphan-<id>."""
    col_rid = (row.get("run_id") or "").strip()
    if col_rid:
        return col_rid
    md = _parse_metadata(row.get("metadata"))
    rid = (md.get("run_id") or "").strip()
    if rid:
        return rid
    return f"orphan-{row.get('id', 0)}"


def aggregate_runs(rows: List[dict]) -> List[dict]:
    """Group rows by effective_run_key; return run summary dicts, newest first."""
    groups: Dict[str, dict] = {}
    for row in rows:
        rk = effective_run_key(row)
        ts = row.get("timestamp") or ""
        cost = float(row.get("cost_usd") or 0)
        lat = float(row.get("latency_ms") or 0)
        err = row.get("error")
        if rk not in groups:
            groups[rk] = {
                "run_key": rk,
                "step_count": 0,
                "started_at": ts,
                "ended_at": ts,
                "total_cost_usd": 0.0,
                "total_latency_ms": 0.0,
                "_latency_sum_ms": 0.0,
                "_session_latency_ms": None,
                "has_error": False,
            }
        g = groups[rk]
        g["step_count"] += 1
        g["total_cost_usd"] += cost
        g["_latency_sum_ms"] += lat
        if is_session_envelope_row(row):
            # Root session envelope spans represent true run wall time for traced agent runs.
            if g["_session_latency_ms"] is None or lat > float(g["_session_latency_ms"] or 0):
                g["_session_latency_ms"] = lat
        if err:
            g["has_error"] = True
        if ts and (not g["started_at"] or ts < g["started_at"]):
            g["started_at"] = ts
        if ts and (not g["ended_at"] or ts > g["ended_at"]):
            g["ended_at"] = ts

    out = []
    for g in groups.values():
        sess = g.get("_session_latency_ms")
        if sess is not None:
            g["total_latency_ms"] = float(sess)
        else:
            st = g.get("started_at") or ""
            en = g.get("ended_at") or ""
            start_ts = None
            end_ts = None
            try:
                from datetime import datetime

                if st:
                    start_ts = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
                if en:
                    end_ts = datetime.fromisoformat(str(en).replace("Z", "+00:00"))
            except Exception:
                start_ts = None
                end_ts = None
            if start_ts is not None and end_ts is not None:
                g["total_latency_ms"] = max((end_ts - start_ts).total_seconds() * 1000.0, 0.0)
            else:
                g["total_latency_ms"] = float(g.get("_latency_sum_ms") or 0.0)
        g.pop("_latency_sum_ms", None)
        g.pop("_session_latency_ms", None)
        out.append(g)
    out.sort(key=lambda x: x.get("ended_at") or "", reverse=True)
    return out


# Heuristic thresholds (tunable)
SLOW_MULT = 2.0  # vs median latency in run
LARGE_INPUT_TOKENS = 8000


def analyze_run(steps: List[dict]) -> dict:
    """
    Per-run insights: failed step index, slowest, costliest, flags for large context / empty output.
    steps should be sorted by timestamp ascending.
    """
    if not steps:
        return {
            "summary_line": "",
            "badges": [],
            "step_flags": [],
        }

    lats = [float(s.get("latency_ms") or 0) for s in steps]
    median_lat = sorted(lats)[len(lats) // 2] if lats else 0
    slow_threshold = max(median_lat * SLOW_MULT, 500)

    step_flags: List[dict] = []
    failed_idx = None
    slow_idx = None
    costly_idx = None
    max_cost = -1.0
    max_slow_lat = -1.0

    for i, s in enumerate(steps):
        flags: List[str] = []
        if s.get("error"):
            flags.append("error")
            if failed_idx is None:
                failed_idx = i
        lat = float(s.get("latency_ms") or 0)
        if lat >= slow_threshold and median_lat > 0:
            flags.append("slow")
            if lat > max_slow_lat:
                max_slow_lat = lat
                slow_idx = i
        cost = float(s.get("cost_usd") or 0)
        if cost > max_cost:
            max_cost = cost
            costly_idx = i
        inp = int(s.get("input_tokens") or 0)
        if inp >= LARGE_INPUT_TOKENS:
            flags.append("large_context")
        resp = (s.get("response") or "").strip()
        if not resp and not s.get("error"):
            flags.append("empty_output")
        step_flags.append({"index": i, "flags": flags})

    parts = []
    if failed_idx is not None:
        parts.append(f"Failed at step {failed_idx + 1}")
    if slow_idx is not None and slow_idx != failed_idx:
        parts.append(f"Slow step: #{slow_idx + 1} (>{slow_threshold:.0f}ms vs median)")
    if costly_idx is not None:
        parts.append(f"Highest cost: step #{costly_idx + 1}")
    # large context
    lc = [i for i, sf in enumerate(step_flags) if "large_context" in sf["flags"]]
    if lc:
        parts.append(f"Large context: step(s) {', '.join(str(i + 1) for i in lc[:3])}")
    # empty output
    eo = [i for i, sf in enumerate(step_flags) if "empty_output" in sf["flags"]]
    if eo:
        parts.append(f"Empty output: step(s) {', '.join(str(i + 1) for i in eo[:3])}")

    badges = []
    if failed_idx is not None:
        badges.append({"id": "failed", "label": "Has failure", "tooltip": "At least one step has error set"})
    if slow_idx is not None:
        badges.append({"id": "slow", "label": "Slow step", "tooltip": f"Flagged: latency ≥ max({slow_threshold:.0f}ms, 2× median)"})
    if any("large_context" in sf["flags"] for sf in step_flags):
        badges.append({"id": "tokens", "label": "Large context", "tooltip": f"Flagged: input_tokens ≥ {LARGE_INPUT_TOKENS}"})
    if any("empty_output" in sf["flags"] for sf in step_flags):
        badges.append({"id": "empty", "label": "Empty output", "tooltip": "Response empty with no error"})

    return {
        "summary_line": " · ".join(parts) if parts else "No issues flagged",
        "badges": badges,
        "step_flags": step_flags,
        "median_latency_ms": median_lat,
        "slow_threshold_ms": slow_threshold,
    }


def step_title(log: dict) -> str:
    sn = (log.get("span_name") or "").strip()
    if sn:
        return sn
    return f"{log.get('provider') or '?'} · {log.get('model') or '?'}"


def effective_span_type(row: dict) -> str:
    st = str(row.get("span_type") or "").strip().lower()
    if st:
        return st
    md = _parse_metadata(row.get("metadata"))
    return str(md.get("span_type") or "").strip().lower()


def _tracer_step_from_prompt(prompt: Any) -> str:
    if not isinstance(prompt, str):
        return ""
    t = prompt.strip()
    if not t.startswith("{"):
        return ""
    try:
        o = json.loads(t)
        if isinstance(o, dict) and o.get("step") is not None:
            return str(o.get("step")).strip()
    except Exception:
        pass
    return ""


def _tool_name_from_prompt(prompt: Any) -> str:
    if not isinstance(prompt, str):
        return ""
    t = prompt.strip()
    if not t.startswith("{"):
        return ""
    try:
        o = json.loads(t)
        if isinstance(o, dict) and o.get("tool") is not None:
            return str(o.get("tool")).strip()
    except Exception:
        pass
    return ""


def _llm_response_snippet(response: Any) -> str:
    if not isinstance(response, str):
        return ""
    r = response.strip()
    if not r:
        return ""
    if r.startswith("[") and '"function"' in r:
        return ""
    first = next((ln.strip() for ln in r.split("\n") if ln.strip()), "")
    if not first:
        return ""
    if first.startswith("{") and len(first) > 200:
        return ""
    return (first[:51] + "…") if len(first) > 52 else first


def _trace_lane_short(md: dict) -> str:
    v = md.get("trace_lane")
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    return s if len(s) <= 28 else s[:27] + "…"


def _handoff_goto_from_tool_row(md: dict, response: Any) -> str:
    for raw in (md.get("tool_result"), response):
        if raw is None:
            continue
        if isinstance(raw, dict):
            g = raw.get("goto")
            if g is not None and str(g).strip():
                return str(g).strip()
        if isinstance(raw, str):
            t = raw.strip()
            if t.startswith("{"):
                try:
                    o = json.loads(t)
                    if isinstance(o, dict) and o.get("goto") is not None:
                        g = str(o.get("goto")).strip()
                        if g:
                            return g
                except Exception:
                    pass
    return ""


def run_step_primary_label(row: dict) -> str:
    md = _parse_metadata(row.get("metadata"))
    top_st = str(row.get("span_type") or "").strip().lower()
    eff = effective_span_type(row)
    if infer_is_tool_span(row):
        tn = md.get("tool_name")
        if tn is not None and str(tn).strip():
            return str(tn).strip()
        fp = _tool_name_from_prompt(row.get("prompt"))
        if fp:
            return fp
        return step_title(row)
    if eff == "agent" or top_st == "agent":
        return step_title(row)
    an = str(md.get("agent_span_name") or "").strip()
    if an:
        return an
    stp = _tracer_step_from_prompt(row.get("prompt"))
    if stp:
        return stp
    snip = _llm_response_snippet(row.get("response"))
    if snip:
        return snip
    model = str(row.get("model") or "").strip()
    if model and model != "unknown":
        return model
    sn = str(row.get("span_name") or "").strip()
    if sn:
        return sn
    return f"{row.get('provider') or '?'} · {row.get('model') or '?'}"


def run_step_trace_display_label(row: dict) -> str:
    md = _parse_metadata(row.get("metadata"))
    base = run_step_primary_label(row)
    eff = effective_span_type(row)
    if infer_is_tool_span(row):
        lane = _trace_lane_short(md)
        g = _handoff_goto_from_tool_row(md, row.get("response"))
        line = base
        if g:
            line = f"{line} → {g}"
        if lane:
            line = f"{lane} · {line}"
        return line
    if eff == "llm":
        lane = _trace_lane_short(md)
        if lane:
            return f"{lane} · {base}"
    return base


def cache_breakdown_for_run(steps: List[dict]) -> dict:
    """
    Per-step token breakdown for prompt cache visualization.
    cached + uncached = prompt (input) tokens; output = completion tokens.
    cache_pct (run) = sum(cached) / sum(prompt_tokens) when prompt total > 0.
    """
    rows: List[dict] = []
    totals = {
        "cached": 0,
        "uncached": 0,
        "output": 0,
        "prompt_tokens": 0,
        "cost_usd": 0.0,
    }
    for s in steps:
        inp = int(s.get("input_tokens") or 0)
        cached = int(s.get("cached_input_tokens") or 0)
        if cached == 0:
            md = s.get("metadata") or {}
            if isinstance(md, dict):
                cached = int(md.get("cache_read_tokens") or md.get("cache_read") or 0)
        cached = max(0, min(cached, inp))
        uncached = max(0, inp - cached)
        out = int(s.get("output_tokens") or 0)
        cost = float(s.get("cost_usd") or 0)
        bar_total = cached + uncached + out
        cache_pct_step = (cached / inp * 100.0) if inp > 0 else None
        rows.append(
            {
                "id": s.get("id"),
                "label": run_step_trace_display_label(s),
                "cached": cached,
                "uncached": uncached,
                "output": out,
                "prompt_tokens": inp,
                "cache_pct": round(cache_pct_step, 2) if cache_pct_step is not None else None,
                "cost_usd": cost,
                "bar_total": bar_total,
            }
        )
        totals["cached"] += cached
        totals["uncached"] += uncached
        totals["output"] += out
        totals["prompt_tokens"] += inp
        totals["cost_usd"] += cost

    pt = totals["prompt_tokens"]
    totals["cache_pct"] = round((totals["cached"] / pt * 100.0), 2) if pt > 0 else None
    totals["has_cached_prompt_data"] = totals["cached"] > 0
    return {"totals": totals, "rows": rows}


def call_trace_segments_for_run_rows(rows: List[dict]) -> List[dict]:
    """Wall-time-weighted agent/llm/tool strip segments (same kinds as ``mo_run_summaries``)."""
    from cognitive.modes import call_kind_for_trace_strip, is_session_envelope_row

    if not rows:
        return []
    work = [r for r in rows if not is_session_envelope_row(r)]
    if not work:
        return []
    total_lat = sum(float(r.get("latency_ms") or 0) for r in work)
    if total_lat <= 0:
        total_lat = float(len(work)) or 1.0
    out: List[dict] = []
    for r in work:
        kind = call_kind_for_trace_strip(r)
        lt = float(r.get("latency_ms") or 0)
        if lt <= 0:
            lt = total_lat / max(len(work), 1)
        frac = round(lt / total_lat, 6)
        out.append({"kind": kind, "fraction": frac})
    return out
