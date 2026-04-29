"""
MiniObserve + LangGraph: compact tool logs, invoke callbacks, traced session helpers.

Align with MiniObserve ``AGENTS.md`` (ingest, keys, LangGraph / LangChain, tool payload size).

Dependencies: ``miniobserve[langchain]``, ``langgraph`` (for ``langgraph.types.Command`` in tool output).

Copy this module into your application tree and import from it, or vendor equivalent logic.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from miniobserve import traced_agent_session
from miniobserve.integrations.langchain_callback import MiniObserveCallbackHandler


def _safe_tool_output_str(output: Any, *, limit: int = 4000) -> str:
    """LangChain tool output → string (subset of SDK behavior; avoids private SDK imports)."""
    if output is None:
        return ""
    c = getattr(output, "content", None)
    if c is not None:
        s = str(c)
        return s if len(s) <= limit else s[: limit - 3] + "..."
    s = str(output)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _goto_preview(goto: Any) -> Any:
    if goto is None:
        return None
    if isinstance(goto, str):
        return goto
    if isinstance(goto, list):
        parts = [str(item)[:120] for item in goto[:5]]
        if len(goto) > 5:
            parts.append("…")
        return parts
    return str(goto)[:200]


def _tool_result_for_miniobserve_logs(output: Any, *, max_len: int = 800) -> str:
    """
    Small ingest payload for tool spans (AGENTS: keep tool log payloads small).

    LangGraph handoff tools often return ``Command``; logging a full ``repr`` bloats rows.
    """
    if output is None:
        return ""
    try:
        from langgraph.types import Command

        if isinstance(output, Command):
            payload: dict[str, Any] = {
                "kind": "langgraph.Command",
                "goto": _goto_preview(output.goto),
            }
            graph = getattr(output, "graph", None)
            if graph is not None:
                payload["graph"] = str(graph)[:120]
            if getattr(output, "update", None) is not None:
                payload["has_update"] = True
            text = json.dumps(payload, default=str)
            return text if len(text) <= max_len else text[: max_len - 15] + "…[truncated]"
    except Exception:
        pass
    text = _safe_tool_output_str(output)
    if len(text) <= max_len:
        return text
    return text[: max_len - 15] + "…[truncated]"


class CompactToolLogMiniObserveCallbackHandler(MiniObserveCallbackHandler):
    """``MiniObserveCallbackHandler`` with bounded ``tool_result`` for ``Command`` / long returns."""

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        key = str(run_id)
        pair = self._pending_tool.get(key)
        if not pair:
            return
        _ctx, s = pair
        s.tool_result = _tool_result_for_miniobserve_logs(output)
        self._last_completed_tool_span_id = s.span_id
        self._exit_tool(run_id, exc_type=None, exc_val=None, exc_tb=None)


def configure_miniobserve_env() -> None:
    """Local OSS defaults: explicit default API key + blocking flush for short-lived CLIs (AGENTS.md)."""
    os.environ.setdefault("MINIOBSERVE_API_KEY", "sk-local-default-key")
    os.environ.setdefault("MINIOBSERVE_TRACER_BLOCKING_FLUSH", "1")


def miniobserve_invoke_config(tracer: Any, root_span: Any) -> dict[str, Any]:
    """Fragment for ``graph.invoke(..., config=...)`` with MiniObserve LangChain callbacks."""
    return {
        "callbacks": [
            CompactToolLogMiniObserveCallbackHandler(
                tracer, root_parent_span_id=root_span.span_id
            )
        ],
    }


def decision_metadata(
    *,
    decision_type: str,
    chosen: str | list[str],
    available: list[str] | None = None,
    selection_signals: dict[str, Any] | None = None,
    expected_downstream: list[str] | None = None,
    impact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build ``metadata.decision`` payload for deterministic decision observability.

    IDs should be namespaced for reliable matching:
    ``tool:<name>``, ``route:<name>``, ``agent:<name>``, ``workflow:<name>``.
    """
    return {
        "decision": {
            "type": str(decision_type or "").strip(),
            "chosen": chosen if isinstance(chosen, list) else [str(chosen)],
            "available": list(available or []),
            "selection_signals": dict(selection_signals or {}),
            "expected_downstream": list(expected_downstream or []),
            "impact": dict(impact or {}),
        }
    }


def workflow_node_metadata(node_id: str, *, route_id: str | None = None) -> dict[str, Any]:
    """
    Canonical orchestration identifier for deterministic path validation.

    Prefer ``workflow_node`` values like ``route:final`` / ``route:research``.
    ``route_id`` is optional compatibility alias.
    """
    out: dict[str, Any] = {"workflow_node": str(node_id or "").strip()}
    if route_id:
        out["route_id"] = str(route_id).strip()
    return out


def export_tracer_ingest_batch(tracer: Any) -> dict[str, Any]:
    """
    Return the JSON body MiniObserve would receive on ``POST .../api/logs`` for this run:
    ``{"logs": [<span dicts in flush order>]}`` plus ``run_id`` (see AGENTS.md **Runs and UI parity**).

    Send the batch with header ``X-MiniObserve-Run-Id`` set to the same ``run_id``.

    Uses the tracer's internal serialization (same as :meth:`Tracer.flush_remote`). Tool
    strings may be truncated by :class:`CompactToolLogMiniObserveCallbackHandler` and by
    the tracer's 4000-char cap on ``tool_result`` in metadata.
    """
    logs = [tracer._span_to_log_body(s) for s in tracer._ordered_spans_for_remote()]
    return {"run_id": str(tracer.run_id).strip(), "logs": logs}


def print_miniobserve_ingest_footer(tracer: Any) -> None:
    """Print whether spans were sent over HTTP vs stdout/off (helps debug empty dashboards)."""
    remote = getattr(tracer, "_remote", None)
    run_id = str(tracer.run_id).strip()
    if remote:
        base = str(getattr(remote, "server_url", "") or "").rstrip("/")
        app = str(getattr(remote, "app_name", "") or "default")
        print(
            f"[miniobserve] HTTP ingest: {base} | app={app} | run_id={run_id!r} "
            f"— open that origin in the browser and search runs for this run_id "
            f"(header X-MiniObserve-Run-Id = run_id).",
            file=sys.stderr,
        )
        return
    raw = (os.environ.get("MINIOBSERVE_URL") or "").strip()
    if raw.lower() in ("stdout", "off", "0", "false"):
        why = f"MINIOBSERVE_URL is {raw!r} (no HTTP; spans only printed above if stdout)"
    else:
        why = "Tracer has no HTTP client (unset MINIOBSERVE_URL is OK → defaults to localhost:7823)"
    print(
        f"[miniobserve] No HTTP ingest — {why}. run_id={run_id!r}. "
        f"If you expected the UI: unset stdout/off, start the backend, and match the URL.",
        file=sys.stderr,
    )


__all__ = [
    "CompactToolLogMiniObserveCallbackHandler",
    "configure_miniobserve_env",
    "decision_metadata",
    "export_tracer_ingest_batch",
    "miniobserve_invoke_config",
    "print_miniobserve_ingest_footer",
    "traced_agent_session",
    "workflow_node_metadata",
]
