"""Run grouping and lightweight analysis from stored logs."""
import json
from collections import defaultdict, deque
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


_DECISION_ALLOWED_PREFIXES = {"tool", "route", "agent", "workflow"}


def _normalize_decision_id(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if ":" in s:
        pref, rest = s.split(":", 1)
        pref = pref.strip()
        rest = rest.strip()
        if pref in _DECISION_ALLOWED_PREFIXES and rest:
            return f"{pref}:{rest}"
        return s
    return f"workflow:{s}"


def _coerce_decision_ids(raw: Any) -> list[str]:
    vals = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    seen: set[str] = set()
    for v in vals:
        nid = _normalize_decision_id(v)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(nid)
    return out


def _decision_block_from_metadata(md: dict) -> dict:
    dec = md.get("decision")
    if not isinstance(dec, dict):
        return {}
    out: dict[str, Any] = {}
    dtype = str(dec.get("type") or "").strip()
    if dtype:
        out["type"] = dtype
    chosen = _coerce_decision_ids(dec.get("chosen"))
    if chosen:
        out["chosen"] = chosen
    available = _coerce_decision_ids(dec.get("available"))
    if available:
        out["available"] = available
    expected = _coerce_decision_ids(dec.get("expected_downstream"))
    if expected:
        out["expected_downstream"] = expected
    signals = dec.get("selection_signals")
    if isinstance(signals, dict):
        out["selection_signals"] = signals
    impact = dec.get("impact")
    if isinstance(impact, dict):
        out["impact"] = impact
    return out


def _observed_identifiers_with_mode(row: dict) -> tuple[set[str], bool, bool]:
    md = _parse_metadata(row.get("metadata"))
    out: set[str] = set()
    used_canonical = False
    used_fallback = False
    tn = str(md.get("tool_name") or "").strip()
    if tn:
        out.add(_normalize_decision_id(f"tool:{tn}"))
        used_canonical = True
    wf = str(md.get("workflow_node") or "").strip()
    if wf:
        out.add(_normalize_decision_id(wf))
        used_canonical = True
    rid = str(md.get("route_id") or "").strip()
    if rid:
        out.add(_normalize_decision_id(f"route:{rid}"))
        used_canonical = True
    an = str(md.get("agent_name") or "").strip()
    if an:
        out.add(_normalize_decision_id(f"agent:{an}"))
        used_fallback = True
    lane = str(md.get("trace_lane") or md.get("mo_trace_lane") or "").strip()
    if lane:
        out.add(_normalize_decision_id(f"route:{lane}"))
        used_fallback = True
    sn = str(row.get("span_name") or "").strip()
    if sn:
        out.add(_normalize_decision_id(f"workflow:{sn}"))
        used_fallback = True
    return ({x for x in out if x}, used_canonical, used_fallback)


def decision_observability_for_run(steps: List[dict]) -> dict:
    """
    Deterministic decision observability derived from `metadata.decision`.

    - skipped = available - chosen
    - missing_expected = expected_downstream - observed_descendants
    - computed impact: descendants latency/tokens/cost/errors
    """
    if not steps:
        return {"decisions": [], "integrity_alerts": []}

    by_id: dict[int, dict] = {}
    children: dict[int, list[int]] = defaultdict(list)
    for s in steps:
        sid = s.get("id")
        if sid is None:
            continue
        try:
            sid_i = int(sid)
        except Exception:
            continue
        by_id[sid_i] = s
    for s in steps:
        sid = s.get("id")
        pid = s.get("parent_span_id")
        if sid is None or pid is None:
            continue
        try:
            sid_i = int(sid)
            pid_i = int(pid)
        except Exception:
            continue
        if pid_i in by_id:
            children[pid_i].append(sid_i)

    decisions: list[dict] = []
    alerts: list[dict] = []

    for s in steps:
        sid = s.get("id")
        if sid is None:
            continue
        try:
            sid_i = int(sid)
        except Exception:
            continue
        md = _parse_metadata(s.get("metadata"))
        decision = _decision_block_from_metadata(md)
        if not decision:
            continue

        q: deque[int] = deque(children.get(sid_i, []))
        desc: list[dict] = []
        seen: set[int] = set()
        while q:
            cur = q.popleft()
            if cur in seen or cur not in by_id:
                continue
            seen.add(cur)
            row = by_id[cur]
            desc.append(row)
            for nxt in children.get(cur, []):
                if nxt not in seen:
                    q.append(nxt)

        observed: set[str] = set()
        observed_canonical: set[str] = set()
        observed_fallback: set[str] = set()
        for d in desc:
            ids, has_canon, has_fallback = _observed_identifiers_with_mode(d)
            observed |= ids
            if has_canon:
                observed_canonical |= ids
            if has_fallback:
                observed_fallback |= ids

        chosen = decision.get("chosen") or []
        available = decision.get("available") or []
        expected = decision.get("expected_downstream") or []
        skipped = [x for x in available if x not in chosen]
        missing = [x for x in expected if x not in observed]
        used_modes = set()
        for x in expected:
            if x in observed_canonical:
                used_modes.add("canonical")
            elif x in observed_fallback:
                used_modes.add("fallback")
        if not used_modes:
            matching_mode = "canonical" if (observed_canonical and not observed_fallback) else ("fallback" if observed_fallback else "canonical")
        elif len(used_modes) > 1:
            matching_mode = "mixed"
        else:
            matching_mode = next(iter(used_modes))

        computed_impact = {
            "descendant_span_count": len(desc),
            "latency_ms": round(sum(float(d.get("latency_ms") or 0.0) for d in desc), 3),
            "cost_usd": round(sum(float(d.get("cost_usd") or 0.0) for d in desc), 8),
            "input_tokens": int(sum(int(d.get("input_tokens") or 0) for d in desc)),
            "output_tokens": int(sum(int(d.get("output_tokens") or 0) for d in desc)),
            "error_count": int(sum(1 for d in desc if d.get("error"))),
        }

        entry = {
            "step_id": sid_i,
            "type": decision.get("type") or "",
            "chosen": chosen,
            "available": available,
            "skipped": skipped,
            "selection_signals": decision.get("selection_signals") or {},
            "expected_downstream": expected,
            "missing_expected": missing,
            "observed_identifiers": sorted(observed),
            "matching_mode": matching_mode,
            "impact": {
                "reported": decision.get("impact") or {},
                "computed": computed_impact,
            },
            "provenance": {
                "selection_signals": "emitted" if isinstance(decision.get("selection_signals"), dict) else "absent",
                "impact_reported": "emitted" if isinstance(decision.get("impact"), dict) else "absent",
                "impact_computed": "computed",
            },
        }
        decisions.append(entry)

        if missing:
            alerts.append(
                {
                    "kind": "missing_expected_path",
                    "step_id": sid_i,
                    "message": f"Expected path not observed: {', '.join(missing)}",
                    "missing": missing,
                    "matching_mode": matching_mode,
                }
            )

    return {"decisions": decisions, "integrity_alerts": alerts}


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


def _short_text(s: Any, max_len: int = 140) -> str:
    t = " ".join(str(s or "").split()).strip()
    if not t:
        return ""
    return t if len(t) <= max_len else (t[: max_len - 1] + "…")


def _query_preview_from_row(row: dict) -> str:
    md = _parse_metadata(row.get("metadata"))
    dec = md.get("decision")
    if isinstance(dec, dict):
        sig = dec.get("selection_signals")
        if isinstance(sig, dict):
            q = sig.get("query")
            if q:
                return _short_text(q)

    msgs = row.get("messages")
    if isinstance(msgs, str):
        try:
            msgs = json.loads(msgs)
        except Exception:
            msgs = None
    if isinstance(msgs, list) and msgs:
        for m in reversed(msgs):
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip().lower()
            if role in ("user", "human") and m.get("content"):
                return _short_text(m.get("content"))

    prompt = row.get("prompt")
    if isinstance(prompt, str):
        p = prompt.strip()
        if p and not p.startswith("{") and p.lower() not in ("route_decision",):
            return _short_text(p)
    return ""


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
                "query_preview": "",
                "_query_preview_ts": "",
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
        qp = _query_preview_from_row(row)
        if qp:
            qpts = str(ts or "")
            if not g["query_preview"] or (qpts and (not g["_query_preview_ts"] or qpts < g["_query_preview_ts"])):
                g["query_preview"] = qp
                g["_query_preview_ts"] = qpts

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
        g.pop("_query_preview_ts", None)
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
