"""
Imperative span tracer for agent demos: stdout mode or batch POST /api/logs.

Uses opaque client_span_id / parent_client_span_id; the server resolves parents.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Optional, Tuple

from .env_url import resolve_miniobserve_http_base_url
from .observer import MiniObserve


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on", "debug")


MINIOBSERVE_DEBUG = _env_truthy("MINIOBSERVE_DEBUG")

_flush_threads_lock = threading.Lock()
_pending_flush_threads: list[threading.Thread] = []
_tracer_flush_atexit_registered = False


def _register_tracer_flush_thread(t: threading.Thread) -> None:
    """So short-lived processes still deliver HTTP batch after ``summary()`` (join on interpreter exit)."""
    global _tracer_flush_atexit_registered
    with _flush_threads_lock:
        _pending_flush_threads.append(t)
        if _tracer_flush_atexit_registered:
            return
        atexit.register(_join_tracer_flush_threads_at_exit)
        _tracer_flush_atexit_registered = True


def _join_tracer_flush_threads_at_exit() -> None:
    with _flush_threads_lock:
        threads = list(_pending_flush_threads)
        _pending_flush_threads.clear()
    for t in threads:
        if t.is_alive():
            t.join(timeout=20.0)
            if t.is_alive() and MINIOBSERVE_DEBUG:
                _debug_print(
                    f"[miniobserve] flush thread {t.name!r} still alive after atexit join timeout — "
                    "set MINIOBSERVE_TRACER_BLOCKING_FLUSH=1 for synchronous flush."
                )


def _resolve_miniobserve_url() -> Optional[str]:
    return resolve_miniobserve_http_base_url()


def _debug_print(msg: str) -> None:
    print(msg, file=sys.stderr)


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def strip_messages_for_log(messages: list) -> list:
    """
    Reduce chat messages to fields suitable for MiniObserve ingest (``request.messages``).

    Drops embeddings / non-JSON-safe extras; keeps role, content, tool_call_id, tool_calls.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        row: dict[str, Any] = {"role": m.get("role", "")}
        if "content" in m:
            row["content"] = m.get("content")
        if "tool_call_id" in m:
            row["tool_call_id"] = m.get("tool_call_id")
        if "tool_calls" in m:
            row["tool_calls"] = m.get("tool_calls")
        out.append(row)
    return out


def print_agent_trace_banner(tracer: "Tracer", *, objective: str, mode: str) -> None:
    """Stdout banner: correlate a local run with ``run_id`` in the MiniObserve UI."""
    bar = "=" * 55
    print(f"\n{bar}")
    print(f"  objective  {objective}")
    print(f"  mode       {mode}")
    print(f"  run        {tracer.run_id}")
    print(f"{bar}\n")


def _apply_llm_result(span: "Span", result: dict[str, Any]) -> None:
    """Fill standard LLM span fields from a normalized ``call_llm``-style dict."""
    u = result.get("usage") or {}
    span.input_tokens = int(u.get("input", 0) or 0)
    span.output_tokens = int(u.get("output", 0) or 0)
    span.cache_read_tokens = int(u.get("cache_read", 0) or 0)
    span.cache_write_tokens = int(u.get("cache_write", 0) or 0)
    span.had_tool_call = result.get("tool_call") is not None
    span.assistant_preview = (result.get("content") or "")[:2000]
    tc = result.get("tool_call")
    if tc:
        span.tool_call_summary = json.dumps(
            {"name": tc.get("name"), "args": tc.get("args")},
            default=str,
        )


def _json_safe_object(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        json.dumps(raw, default=str)
        return raw
    except Exception:
        return {str(k): str(v) for k, v in raw.items()}


@dataclass
class Span:
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    run_id: str = ""
    parent_span_id: Optional[str] = None
    span_type: str = "llm"
    name: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None

    model: Optional[str] = None
    provider: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    had_tool_call: bool = False
    prev_tool_span_id: Optional[str] = None
    prompt_fingerprint: Optional[dict] = None
    system_prompt_preview: Optional[str] = None
    request_messages: Optional[list] = None
    assistant_preview: Optional[str] = None
    tool_call_summary: Optional[str] = None
    trace_lane: Optional[str] = None
    # LangGraph node / logical agent → metadata.agent_name (HTTP clients may set the same key).
    agent_name: Optional[str] = None

    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None
    tool_call_index: int = 0

    error: Optional[str] = None
    objective_met: bool = False
    extra_metadata: Optional[dict] = None

    def finish(self) -> None:
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 1)

    def fingerprint_prompt(self, messages: list) -> dict:
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        return {
            "system_hash": hashlib.md5(str(system).encode()).hexdigest()[:8],
            "system_length": len(str(system).split()),
            "num_messages": len(messages),
        }


class Tracer:
    """Collect spans and flush via POST /api/logs (or pretty-print locally)."""

    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        server_url: Optional[str] = None,
        app_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.run_id = (run_id or uuid.uuid4().hex[:12]).strip()
        self.spans: list[Span] = []
        self._tool_call_counts: dict[str, int] = {}
        url = server_url if server_url is not None else _resolve_miniobserve_url()
        self._remote: Optional[MiniObserve] = None
        if url:
            resolved_app = (
                (app_name if app_name is not None else (os.getenv("MINIOBSERVE_APP_NAME") or "")).strip()
                or "default"
            )
            self._remote = MiniObserve(
                server_url=url,
                app_name=resolved_app,
                api_key=api_key or (os.getenv("MINIOBSERVE_API_KEY") or "").strip() or None,
            )

    @contextmanager
    def span(self, span_type: str, name: str, parent_id: Optional[str] = None):
        s = Span(
            run_id=self.run_id,
            span_type=span_type,
            name=name,
            parent_span_id=parent_id,
        )
        try:
            yield s
        except Exception as e:
            s.error = str(e)
            raise
        finally:
            s.finish()
            self._track_tool_loop(s)
            self.spans.append(s)
            self._emit(s)

    def _track_tool_loop(self, s: Span) -> None:
        if s.span_type != "tool":
            return
        key = f"{s.tool_name}:{json.dumps(s.tool_args, sort_keys=True, default=str)}"
        self._tool_call_counts[key] = self._tool_call_counts.get(key, 0) + 1
        s.tool_call_index = self._tool_call_counts[key]

    def _emit(self, s: Span) -> None:
        if self._remote:
            return
        self._pretty_print(s)

    def _ordered_spans_for_remote(self) -> list[Span]:
        """
        Order spans for POST /api/logs so each ``parent_client_span_id`` refers to an earlier
        entry in the batch (server id_map resolution).

        Previously we used ``agents[:1] + others``, which dropped every ``agent`` span after
        the first — nested routers / multi-segment agent traces then ingested as a single step.
        """
        spans = list(self.spans)
        if len(spans) <= 1:
            return spans
        by_client = {s.span_id: s for s in spans if getattr(s, "span_id", None)}
        order_idx = {id(s): i for i, s in enumerate(spans)}
        children: dict[str, list[Span]] = defaultdict(list)
        indeg: dict[str, int] = {s.span_id: 0 for s in spans}
        for s in spans:
            p = s.parent_span_id
            if p and p in by_client:
                children[p].append(s)
                indeg[s.span_id] += 1
        queue = [s for s in spans if indeg[s.span_id] == 0]
        queue.sort(key=lambda s: order_idx[id(s)])
        out: list[Span] = []
        while queue:
            s = queue.pop(0)
            out.append(s)
            for c in children.get(s.span_id, []):
                indeg[c.span_id] -= 1
                if indeg[c.span_id] == 0:
                    queue.append(c)
                    queue.sort(key=lambda x: order_idx[id(x)])
        if len(out) != len(spans):
            seen = {id(s) for s in out}
            for s in spans:
                if id(s) not in seen:
                    out.append(s)
        return out

    def _span_to_log_body(self, s: Span) -> dict[str, Any]:
        latency = int(round(s.duration_ms or 0))
        meta: dict[str, Any] = {
            "agent_span_name": s.name,
            "span_type": s.span_type,
        }
        if s.error:
            meta["error"] = s.error
        if s.span_type == "llm" and s.prompt_fingerprint:
            meta["prompt_fingerprint"] = s.prompt_fingerprint
        if s.span_type == "llm" and s.system_prompt_preview:
            meta["system_prompt_preview"] = s.system_prompt_preview
        if s.span_type == "llm" and s.cache_read_tokens:
            meta["cache_read_tokens"] = s.cache_read_tokens
        if s.span_type == "llm" and s.cache_write_tokens:
            meta["cache_write_tokens"] = s.cache_write_tokens
        if s.span_type == "tool":
            meta["tool_name"] = s.tool_name
            meta["tool_args"] = s.tool_args
            meta["tool_call_index"] = s.tool_call_index
            if s.tool_result:
                meta["tool_result"] = (s.tool_result or "")[:4000]
        if s.span_type in ("llm", "tool") and s.trace_lane:
            tl = str(s.trace_lane).strip()
            if tl:
                meta["trace_lane"] = tl[:128]
        if s.span_type in ("llm", "tool", "agent") and s.agent_name:
            an = str(s.agent_name).strip()
            if an:
                meta["agent_name"] = an[:128]
        extra = _json_safe_object(s.extra_metadata)
        if extra:
            for k, v in extra.items():
                if k in ("agent_span_name", "span_type"):
                    continue
                meta[k] = v
        # AGENTS.md: optional ISO bounds for timelines (latency_ms remains client wall duration).
        try:
            meta["started_at"] = _iso_utc(float(s.start_time))
        except (TypeError, ValueError, OSError):
            pass
        if s.end_time is not None:
            try:
                meta["ended_at"] = _iso_utc(float(s.end_time))
            except (TypeError, ValueError, OSError):
                pass

        body: dict[str, Any] = {
            "run_id": self.run_id,
            "latency_ms": latency,
            "input_tokens": s.input_tokens if s.span_type == "llm" else 0,
            "output_tokens": s.output_tokens if s.span_type == "llm" else 0,
            "metadata": meta,
            "client_span_id": s.span_id,
        }

        if s.parent_span_id:
            body["parent_client_span_id"] = s.parent_span_id

        if s.span_type == "agent":
            body["span_name"] = "router"
            body["model"] = (s.model or "agent").strip()
            body["provider"] = "agent"
            body["prompt"] = f"agent run: {s.name}"
            body["response"] = json.dumps(
                {"run_id": self.run_id, "objective_met": s.objective_met}
            )
        elif s.span_type == "llm":
            body["span_name"] = "llm_call"
            body["model"] = s.model or ""
            body["provider"] = s.provider or "openai"
            pf = s.prompt_fingerprint or {}
            body["prompt"] = json.dumps(
                {"step": s.name, "fingerprint": pf, "had_tool_call": s.had_tool_call},
                default=str,
            )
            parts = []
            if s.assistant_preview:
                parts.append(s.assistant_preview)
            if s.tool_call_summary:
                parts.append(f"tool_call: {s.tool_call_summary}")
            body["response"] = "\n".join(parts) if parts else ""
            if s.request_messages:
                body["request"] = {"messages": s.request_messages}
        else:
            body["span_name"] = "tool_call"
            body["model"] = ""
            body["provider"] = "tool"
            body["prompt"] = json.dumps({"tool": s.tool_name, "args": s.tool_args}, default=str)
            tr = (s.tool_result or "")[:4000]
            body["response"] = tr

        return body

    def flush_remote(self) -> None:
        if not self._remote:
            return
        logs = [self._span_to_log_body(s) for s in self._ordered_spans_for_remote()]
        if not logs:
            return
        data = self._remote.post_logs_batch(logs, run_id=self.run_id)
        if MINIOBSERVE_DEBUG and data:
            _debug_print(f"[miniobserve] batch ok run={self.run_id} results={data.get('results')}")

    def _flush_remote_safe(self) -> None:
        try:
            self.flush_remote()
        except Exception as exc:
            if MINIOBSERVE_DEBUG:
                _debug_print(f"[miniobserve] flush failed run={self.run_id} error={exc!s}")

    def _flush_remote_non_blocking(self) -> None:
        if not self._remote:
            return
        # Keep observability best-effort: never block agent completion on MiniObserve network I/O.
        t = threading.Thread(
            target=self._flush_remote_safe,
            name=f"miniobserve-flush-{self.run_id}",
            daemon=True,
        )
        t.start()
        _register_tracer_flush_thread(t)

    def _pretty_print(self, s: Span) -> None:
        icons = {"llm": "🧠", "tool": "🔧", "agent": "🤖"}
        mode = self._classify_mode(s)
        mode_colors = {
            "routing": "\033[35m",
            "acting": "\033[32m",
            "observing": "\033[34m",
            "dispatching": "\033[36m",
            "waiting": "\033[33m",
            "stuck": "\033[31m",
            "agent": "\033[37m",
            "unknown": "\033[90m",
        }
        reset = "\033[0m"
        color = mode_colors.get(mode, "")
        icon = icons.get(s.span_type, "?")
        indent = "  " if s.parent_span_id else ""
        loop_warn = f" ⚠️  LOOP ×{s.tool_call_index}" if s.tool_call_index >= 3 else ""
        print(
            f"{indent}{icon} {color}[{mode:10}]{reset} "
            f"{s.name:<28} "
            f"{(s.duration_ms or 0):>6.0f}ms  "
            f"{'err:'+s.error[:20] if s.error else ''}"
            f"{loop_warn}"
        )
        if s.span_type == "llm" and (s.input_tokens or s.output_tokens):
            tok = f"in={s.input_tokens} out={s.output_tokens}"
            if s.cache_read_tokens:
                tok += f" cache_read={s.cache_read_tokens}"
            print(f"{indent}   └─ tokens: {tok}")

    def _classify_mode(self, s: Span) -> str:
        if s.span_type == "agent":
            return "agent"
        if s.span_type == "tool":
            if s.tool_call_index >= 3:
                return "stuck"
            return "acting"
        if s.span_type == "llm":
            an = (getattr(s, "agent_name", None) or "").strip().lower()
            if an == "supervisor":
                return "routing"
            if s.prev_tool_span_id:
                return "dispatching" if s.had_tool_call else "observing"
            return "acting"
        return "unknown"

    def run_llm(
        self,
        *,
        name: str,
        parent_id: str,
        messages: list,
        model: str,
        provider: str,
        prev_tool_span_id: Optional[str],
        fn: Callable[[], dict[str, Any]],
        trace_lane: Optional[str] = None,
        agent_name: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        One LLM call inside an ``llm`` span: set model/provider/fingerprint/request, run ``fn``,
        apply token + preview fields from the result. Sets ``objective_met`` on the span when
        the model returns without a tool call (final answer for this step).

        Optional ``trace_lane`` is copied into ``metadata.trace_lane`` for dashboard trace labels.
        Optional ``agent_name`` is copied into ``metadata.agent_name`` (e.g. LangGraph supervisor
        vs subagent); see AGENTS.md.
        Optional ``extra_metadata`` is merged into span metadata (e.g. ``decision`` block,
        canonical ``workflow_node`` / ``route_id`` IDs for deterministic path checks).
        """
        with self.span("llm", name, parent_id=parent_id) as s:
            s.model = model
            s.provider = provider
            s.prev_tool_span_id = prev_tool_span_id
            if trace_lane is not None:
                s.trace_lane = str(trace_lane).strip() or None
            if agent_name is not None:
                s.agent_name = str(agent_name).strip() or None
            if extra_metadata is not None:
                s.extra_metadata = _json_safe_object(extra_metadata) or None
            s.prompt_fingerprint = s.fingerprint_prompt(messages)
            system_prompt = next((m.get("content") for m in messages if m.get("role") == "system"), "")
            s.system_prompt_preview = str(system_prompt or "")[:500]
            s.request_messages = strip_messages_for_log(messages)
            result = fn()
            _apply_llm_result(s, result)
            s.objective_met = not bool(result.get("tool_call"))
            return result

    def run_tool(
        self,
        *,
        name: str,
        parent_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        fn: Callable[[], str],
        agent_name: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        One tool call inside a ``tool`` span. ``fn`` should execute the tool and return the string
        payload (e.g. JSON). Returns ``(tool_result, client_span_id)`` for linking the next LLM span
        via ``prev_tool_span_id``.

        Optional ``agent_name`` is copied into ``metadata.agent_name`` for UI grouping.
        Optional ``extra_metadata`` is merged into span metadata (e.g. ``decision`` block,
        canonical ``workflow_node`` / ``route_id`` IDs for deterministic path checks).
        """
        with self.span("tool", name, parent_id=parent_id) as s:
            s.tool_name = tool_name
            s.tool_args = tool_args
            if agent_name is not None:
                s.agent_name = str(agent_name).strip() or None
            if extra_metadata is not None:
                s.extra_metadata = _json_safe_object(extra_metadata) or None
            out = fn()
            s.tool_result = out
            return out, s.span_id

    def summary(self) -> None:
        # Default: async flush (non-blocking). Short-lived CLIs: interpreter exit waits for pending
        # flush threads (atexit join, ~20s cap). For immediate delivery before summary() returns, set
        # MINIOBSERVE_TRACER_BLOCKING_FLUSH=1.
        if _env_truthy("MINIOBSERVE_TRACER_BLOCKING_FLUSH"):
            self._flush_remote_safe()
        else:
            self._flush_remote_non_blocking()
        tool_spans = [s for s in self.spans if s.span_type == "tool"]
        llm_spans = [s for s in self.spans if s.span_type == "llm"]
        if (_env_truthy("MINIOBSERVE_TRACER_DIAG") or MINIOBSERVE_DEBUG) and not tool_spans:
            for s in llm_spans:
                if s.had_tool_call or (s.tool_call_summary or "").strip():
                    _debug_print(
                        "[miniobserve] LLM span(s) report tool calls but no tool spans were emitted. "
                        "For LangGraph / LangChain use MiniObserveCallbackHandler (see AGENTS.md)."
                    )
                    break
        total_ms = sum(s.duration_ms or 0 for s in self.spans if s.span_type != "agent")
        tool_ms = sum(s.duration_ms or 0 for s in tool_spans)
        print("\n" + "─" * 55)
        print(f"  run_id   {self.run_id}")
        print(f"  llm calls   {len(llm_spans)}   tool calls  {len(tool_spans)}")
        unique = len({s.tool_name for s in tool_spans})
        print(f"  unique tools {unique}")
        if total_ms:
            print(f"  tool time    {tool_ms:.0f}ms / {total_ms:.0f}ms  ({100 * tool_ms / total_ms:.0f}%)")
        loops = [s for s in tool_spans if s.tool_call_index >= 3]
        if loops:
            print(f"  \033[31m⚠  stuck: {loops[0].tool_name} called ×{loops[0].tool_call_index}\033[0m")
        print("─" * 55 + "\n")


@contextmanager
def traced_agent_session(
    *,
    mode: str,
    objective: str,
    tracer: Optional["Tracer"] = None,
    print_banner: bool = True,
) -> Generator[Tuple[Tracer, Span], None, None]:
    """
    Standard wrapper for one agent job = one trace.

    - Builds a :class:`Tracer` (or uses ``tracer``).
    - Optionally prints a stdout banner with ``run_id``.
    - Opens root ``agent`` / ``agent-root`` span with ``name = agent/{mode}``.
    - Yields ``(tracer, root_span)`` for nested :meth:`Tracer.run_llm` / :meth:`Tracer.run_tool` /
      :meth:`Tracer.span` calls.
    - On exit, calls :meth:`Tracer.summary` (flush + digest). Async HTTP flush is joined on process exit;
      for scripts that must flush before returning from ``summary()``, set ``MINIOBSERVE_TRACER_BLOCKING_FLUSH=1``.

    Environment: ``MINIOBSERVE_URL``, ``MINIOBSERVE_DASHBOARD_ORIGIN`` (when URL unset, same origin as the browser),
    ``MINIOBSERVE_API_KEY``, ``MINIOBSERVE_APP_NAME`` (see :class:`Tracer`).
    """
    t = tracer or Tracer()
    if print_banner:
        print_agent_trace_banner(t, objective=objective, mode=mode)
    with t.span("agent", "agent-root") as root_span:
        root_span.name = f"agent/{mode}"
        yield t, root_span
    t.summary()


def run_quick_probe() -> str:
    """Post a tiny synthetic trace (no LLM). CLI: ``miniobserve quick`` (sets blocking flush)."""
    tracer = Tracer()
    with tracer.span("agent", "agent-root") as root:
        root.name = "agent/quick-probe"
        root.model = "probe-router"
        with tracer.span("llm", "llm-probe", parent_id=root.span_id) as llm:
            llm.model = "gpt-4o-mini"
            llm.provider = "openai"
            llm.input_tokens = 12
            llm.output_tokens = 3
            llm.had_tool_call = True
            llm.assistant_preview = "[quick probe — no real inference]"
            llm.tool_call_summary = '{"name": "search", "args": {"query": "probe"}}'
            llm.prompt_fingerprint = {"probe": True, "mode": "quick"}
        with tracer.span("tool", "search", parent_id=root.span_id) as tool:
            tool.tool_name = "search"
            tool.tool_args = {"query": "miniobserve quick probe"}
            tool.tool_result = "ok"
    tracer.summary()
    print(f"[quick] done run_id={tracer.run_id} spans={len(tracer.spans)}")
    return tracer.run_id
