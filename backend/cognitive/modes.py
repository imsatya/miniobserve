"""Cognitive phase classification for agent traces (pure functions; used at ingest)."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Tuple

# Behavioral phases (framework-agnostic):
#   thinking   — LLM call that did not emit tool calls (no tools seen yet in the turn)
#   calling    — LLM call that emitted tool calls (dispatching work)
#   synthesizing   — LLM call after tools have run, that did not emit further tool calls
#   executing  — tool span or child agent wrapper executing real work
#   unclassified — fell through all heuristics
PHASES = ("thinking", "calling", "synthesizing", "executing", "unclassified")

STUCK_REPEAT_THRESHOLD = 3
WAITING_MULT = 2.0

# Clients sometimes mirror default model/provider ``unknown`` into ``span_type``; treat like unset.
_SPAN_TYPE_PLACEHOLDERS = frozenset({"unknown", "none", "null", "n/a", "na", ""})

# Standard + common integration spellings (LangChain / LangGraph / OTel-ish).
_LLM_SPAN_TYPES = frozenset(
    {
        "llm",
        "chat",
        "completion",
        "embedding",
        "rerank",
        "moderation",
        "message",
        "assistant",
        "ai",
        "inference",
        "invoke",
        "generative",
        "language_model",
        "chatmodel",
    }
)

_LLMISH_PROVIDERS = frozenset(
    {
        "openai",
        "anthropic",
        "azure",
        "google",
        "vertex",
        "gemini",
        "groq",
        "together",
        "cohere",
        "mistral",
        "fireworks",
        "perplexity",
        "deepseek",
        "ollama",
        "xai",
        "openrouter",
        "litellm",
        "bedrock",
        "sagemaker",
        "watson",
        "ai21",
        "voyage",
    }
)


def _span_type_for_kind(row: dict) -> str:
    st = (row.get("span_type") or "").strip().lower()
    return "" if st in _SPAN_TYPE_PLACEHOLDERS else st


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


def is_tool_span(row: dict) -> bool:
    """Strict: only explicit span_type / provider."""
    st = (row.get("span_type") or "").strip().lower()
    if st == "tool":
        return True
    return (row.get("provider") or "").strip().lower() == "tool"


def infer_is_tool_span(row: dict) -> bool:
    """
    Heuristic tool detection so clients need not set span_type=tool everywhere.

    Treats as tool when: explicit tool span/provider; span_type like function/mcp;
    metadata has tool_name, tool_calls, tool_result+name; or typical non-LLM tool row
    (zero tokens + short model name + provider not a known LLM vendor).
    """
    if is_tool_span(row):
        return True
    st = (row.get("span_type") or "").strip().lower()
    if st in ("function", "tool_call", "mcp", "executable", "retrieval"):
        return True
    md = _parse_metadata(row.get("metadata"))
    if (md.get("tool_name") or "").strip():
        return True
    if md.get("tool_result") is not None and ((md.get("tool_name") or "").strip() or (md.get("name") or "").strip()):
        return True
    tc = md.get("tool_calls")
    if isinstance(tc, list) and len(tc) > 0:
        return True
    if isinstance(tc, str) and tc.strip().startswith("["):
        try:
            parsed = json.loads(tc.strip())
        except json.JSONDecodeError:
            return False
        if isinstance(parsed, list) and len(parsed) > 0:
            return True
        return False
    return False


def call_kind_for_trace_strip(row: dict) -> str:
    """
    UI strip bucket aligned with dashboard span colors (not cognitive_mode).

    agent  → outer agent spans (purple)
    llm    → model calls (green)
    tool   → tool execution (yellow)
    other  → rare span_types (gray)
    """
    if infer_is_tool_span(row):
        return "tool"
    st = _span_type_for_kind(row)
    if st == "agent":
        return "agent"
    if _is_llm_like_row(row):
        return "llm"
    return "other"


def is_session_envelope_row(row: dict) -> bool:
    """
    Root session span from the Python tracer: ``span_type`` agent, no parent, ``span_name`` router
    or ``metadata.agent_span_name`` like ``agent/normal``. It wraps the whole run — do not assign
    cognitive_mode or attribute its wall time to summaries.
    """
    st = (row.get("span_type") or "").strip().lower()
    if st != "agent":
        return False
    pid = row.get("parent_span_id")
    if pid is not None and str(pid).strip() != "":
        return False
    sn = (row.get("span_name") or "").strip().lower()
    if sn == "router":
        return True
    md = _parse_metadata(row.get("metadata"))
    an = str(md.get("agent_span_name") or "").strip().lower()
    if an.startswith("agent/"):
        return True
    return False


def tool_arg_fingerprint(row: dict) -> str:
    """Stable key for repeat detection (same tool + same args)."""
    md = _parse_metadata(row.get("metadata"))
    name = (md.get("tool_name") or row.get("model") or "").strip()
    args = md.get("tool_args")
    if args is None:
        args = ""
    elif not isinstance(args, str):
        try:
            args = json.dumps(args, sort_keys=True, default=str)
        except Exception:
            args = str(args)
    if not args.strip():
        p = (row.get("prompt") or "")[:1500]
        args = p
    return f"{name}\0{args[:2000]}"


def _prompt_looks_like_tracer_llm_step(row: dict) -> bool:
    """Python ``Tracer`` stores human step title in ``prompt`` JSON with ``step`` + ``fingerprint`` keys."""
    p = row.get("prompt")
    if not isinstance(p, str):
        return False
    t = p.strip()
    if not t.startswith("{"):
        return False
    try:
        o = json.loads(t)
    except json.JSONDecodeError:
        return False
    if not isinstance(o, dict):
        return False
    if o.get("tool") is not None:
        return False
    return "step" in o and "fingerprint" in o


def _is_llm_like_row(row: dict) -> bool:
    if infer_is_tool_span(row):
        return False
    st = _span_type_for_kind(row)
    if st in _LLM_SPAN_TYPES:
        return True
    if not st:
        return True
    tok = float(row.get("input_tokens") or 0) + float(row.get("output_tokens") or 0)
    if tok > 0 and st != "agent":
        return True
    prov = (row.get("provider") or "").strip().lower()
    mod = (row.get("model") or "").strip().lower()
    if prov in _LLMISH_PROVIDERS and mod and mod not in _SPAN_TYPE_PLACEHOLDERS and st != "agent":
        return True
    sn = (row.get("span_name") or "").strip().lower()
    if sn == "llm_call" or "llm" in sn:
        return True
    if _prompt_looks_like_tracer_llm_step(row) and st != "agent":
        return True
    return False


def _prompt_has_tool_call(row: dict) -> bool:
    """
    Tracer stores step metadata in ``prompt`` as JSON with a ``had_tool_call`` bool.
    Returns True when that flag is explicitly True (covers Tracer-style LLM spans).
    """
    p = row.get("prompt")
    if not isinstance(p, str):
        return False
    t = p.strip()
    if not t.startswith("{"):
        return False
    try:
        o = json.loads(t)
    except json.JSONDecodeError:
        return False
    if not isinstance(o, dict):
        return False
    return bool(o.get("had_tool_call"))


def _llm_response_is_tool_calls_blob(row: dict) -> bool:
    """
    Detect LLM responses that are tool-call emissions, in two formats:

    1. Raw OpenAI format: JSON array starting with ``[`` containing
       ``{type: "function", function: {name, arguments}}`` objects.
    2. Tracer/callback format: string starting with ``tool_call: [``
       containing ``[{name, args}]`` objects (written by MiniObserveCallbackHandler).
    """
    r = (row.get("response") or "").strip()
    if not r:
        return False

    # Format 2: "tool_call: [{name: ..., args: ...}]"
    if r.startswith("tool_call:"):
        rest = r[len("tool_call:"):].strip()
        if rest.startswith("["):
            try:
                j = json.loads(rest)
                if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict) and j[0].get("name"):
                    return True
            except json.JSONDecodeError:
                pass
        return False

    # Format 1: raw OpenAI JSON array
    if not r.startswith("["):
        return False
    try:
        j = json.loads(r)
    except json.JSONDecodeError:
        return False
    if not isinstance(j, list) or len(j) == 0:
        return False
    first = j[0]
    if not isinstance(first, dict):
        return False
    if first.get("type") == "function":
        return True
    fn = first.get("function")
    if isinstance(fn, dict) and (fn.get("name") is not None or fn.get("arguments") is not None):
        return True
    return False


def compute_cognitive_for_run(
    steps: List[dict],
) -> Tuple[Dict[int, str], Dict[int, bool], Dict[int, bool], Dict[str, float], List[dict], List[dict], List[dict]]:
    """
    Returns:
      phases_by_id — routing | planning | acting | observing | dispatching | unknown | "" (session envelope)
      stuck_by_id — repeat-tool anomaly (does not replace phase; tool rows stay ``acting``)
      waiting_by_id — slow tool vs median for same tool name
      mode_fractions (time-weighted by latency_ms, base phases only)
      fingerprint_segments (ordered by timestamp): {mode, fraction}
      stuck_alerts
      call_trace_segments: {kind, fraction} — agent|llm|tool|other (UI strip)
    """
    empty = {}, {}, {}, {}, [], [], []
    if not steps:
        return empty

    rows = []
    for s in steps:
        sid = int(s.get("id") or 0)
        if not sid:
            continue
        rows.append(s)
    if not rows:
        return empty

    rows.sort(key=lambda x: (str(x.get("timestamp") or ""), int(x.get("id") or 0)))

    fp_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        if not infer_is_tool_span(r):
            continue
        fp = tool_arg_fingerprint(r)
        fp_groups[fp].append(r)

    stuck_ids: set[int] = set()
    stuck_alerts: List[dict] = []
    for fp, group in fp_groups.items():
        if len(group) < STUCK_REPEAT_THRESHOLD:
            continue
        group.sort(key=lambda x: (str(x.get("timestamp") or ""), int(x.get("id") or 0)))
        for g in group:
            stuck_ids.add(int(g["id"]))
        costs = [float(g.get("cost_usd") or 0) for g in group]
        total_c = sum(costs)
        wasted = max(0.0, total_c - (costs[0] if costs else 0))
        md0 = _parse_metadata(group[0].get("metadata"))
        tool_name = (md0.get("tool_name") or group[0].get("model") or "tool")[:120]
        args_preview = (md0.get("tool_args") or "")[:80]
        if len(args_preview) == 80:
            args_preview += "…"
        stuck_alerts.append(
            {
                "tool_name": tool_name,
                "count": len(group),
                "wasted_cost_usd": round(wasted, 4),
                "args_preview": args_preview,
            }
        )

    lat_by_tool: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        if not infer_is_tool_span(r):
            continue
        lat = float(r.get("latency_ms") or 0)
        name = (r.get("model") or "tool").strip() or "tool"
        lat_by_tool[name].append(lat)

    def median_lat(name: str) -> float:
        xs = sorted(lat_by_tool.get(name) or [0])
        if not xs:
            return 0.0
        mid = len(xs) // 2
        return float(xs[mid]) if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0

    phases_by_id: Dict[int, str] = {}
    stuck_by_id: Dict[int, bool] = {}
    waiting_by_id: Dict[int, bool] = {}

    n_tools_seen = 0
    prev_was_calling = False

    for r in rows:
        sid = int(r["id"])

        if is_session_envelope_row(r):
            phases_by_id[sid] = ""
            stuck_by_id[sid] = False
            waiting_by_id[sid] = False
            continue

        if infer_is_tool_span(r):
            lat = float(r.get("latency_ms") or 0)
            tname = (r.get("model") or "tool").strip() or "tool"
            med = median_lat(tname)
            is_stuck = sid in stuck_ids
            is_waiting = (not is_stuck) and med > 0 and lat > WAITING_MULT * med
            phases_by_id[sid] = "executing"
            stuck_by_id[sid] = is_stuck
            waiting_by_id[sid] = is_waiting
            n_tools_seen += 1
            prev_was_calling = False
            continue

        if _is_llm_like_row(r):
            emits_tool_calls = _prompt_has_tool_call(r) or _llm_response_is_tool_calls_blob(r)
            if emits_tool_calls:
                phases_by_id[sid] = "calling"
                prev_was_calling = True
            else:
                if n_tools_seen > 0 or prev_was_calling:
                    phases_by_id[sid] = "synthesizing"
                else:
                    phases_by_id[sid] = "thinking"
                prev_was_calling = False
            stuck_by_id[sid] = False
            waiting_by_id[sid] = False
            continue

        # Child agent spans (sub-agent wrappers in LangGraph nested subgraphs):
        # span_type="agent" with a parent — structural containers whose children
        # carry the real cognitive phases. Exclude like session envelopes so their
        # wall time does not double-count in the phase strip.
        if _span_type_for_kind(r) == "agent":
            phases_by_id[sid] = ""
        else:
            phases_by_id[sid] = "unclassified"
        stuck_by_id[sid] = False
        waiting_by_id[sid] = False

    rows_cog = [
        s for s in rows
        if not is_session_envelope_row(s) and phases_by_id.get(int(s["id"])) != ""
    ]
    total_lat = sum(float(s.get("latency_ms") or 0) for s in rows_cog)
    if total_lat <= 0:
        total_lat = float(len(rows_cog)) or 1.0

    mode_time: Dict[str, float] = defaultdict(float)
    for r in rows_cog:
        sid = int(r["id"])
        m = phases_by_id.get(sid, "unknown")
        if not m:
            continue
        lt = float(r.get("latency_ms") or 0)
        if lt <= 0:
            lt = total_lat / max(len(rows_cog), 1)
        mode_time[m] += lt

    mode_fractions = {m: round(mode_time.get(m, 0) / total_lat, 4) for m in PHASES if mode_time.get(m, 0) > 0}
    if not mode_fractions:
        mode_fractions = {"unclassified": 1.0}

    fingerprint_segments: List[dict] = []
    call_trace_segments: List[dict] = []
    for r in rows_cog:
        sid = int(r["id"])
        m = phases_by_id.get(sid, "unknown")
        lt = float(r.get("latency_ms") or 0)
        if lt <= 0:
            lt = total_lat / max(len(rows_cog), 1)
        frac = round(lt / total_lat, 6)
        fingerprint_segments.append({"mode": m, "fraction": frac})
        call_trace_segments.append({"kind": call_kind_for_trace_strip(r), "fraction": frac})

    return (
        phases_by_id,
        stuck_by_id,
        waiting_by_id,
        mode_fractions,
        fingerprint_segments,
        stuck_alerts,
        call_trace_segments,
    )
