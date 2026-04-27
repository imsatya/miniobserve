"""
LangChain Core callback handler → :class:`miniobserve.tracer.Tracer` spans.

Use with any Runnable that propagates LangChain callbacks (including LangGraph
``compiled.invoke(..., config={"callbacks": [...]})``).

Requires: ``pip install langchain-core`` (or ``pip install 'miniobserve[langchain]'`` from this repo).
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler

from ..tracer import Span, Tracer, strip_messages_for_log

if TYPE_CHECKING:
    from uuid import UUID


def miniobserve_langchain_callbacks(
    tracer: Tracer,
    *,
    root_parent_span_id: Optional[str] = None,
) -> List[MiniObserveCallbackHandler]:
    """
    Return a one-element callbacks list for ``RunnableConfig(callbacks=...)``.

    ``root_parent_span_id`` should be the agent root span's ``span_id`` when using
    :func:`miniobserve.tracer.traced_agent_session` so model/tool spans nest under it.
    """
    return [MiniObserveCallbackHandler(tracer, root_parent_span_id=root_parent_span_id)]


def _safe_str(x: Any, limit: int = 4000) -> str:
    if x is None:
        return ""
    s = str(x)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _tool_args_from_inputs(serialized: Dict[str, Any], input_str: str, inputs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(inputs, dict) and inputs:
        try:
            return json.loads(json.dumps(inputs, default=str))
        except Exception:
            pass
    t = (input_str or "").strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            parsed = json.loads(t)
            return parsed if isinstance(parsed, dict) else {"input": parsed}
        except json.JSONDecodeError:
            pass
    return {"input": input_str} if input_str else {}


def _lc_messages_to_dicts(messages: List[Any]) -> List[Dict[str, Any]]:
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    out: List[Dict[str, Any]] = []
    for m in messages:
        r = getattr(m, "type", None) or "user"
        r = role_map.get(str(r), str(r))
        c = getattr(m, "content", None)
        if isinstance(c, list):
            c = json.dumps(c, default=str)
        row: Dict[str, Any] = {"role": r, "content": _safe_str(c, 8000)}
        if getattr(m, "tool_call_id", None):
            row["tool_call_id"] = m.tool_call_id
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            row["tool_calls"] = tcs
        out.append(row)
    return out


def _flatten_chat_batches(messages: List[List[Any]]) -> List[Any]:
    return [m for batch in messages or [] for m in batch]


def _serialized_model_label(serialized: Dict[str, Any]) -> str:
    kwargs = serialized.get("kwargs") or {}
    for key in ("model", "model_name", "model_id"):
        v = kwargs.get(key)
        if v:
            return str(v)
    name = serialized.get("name")
    if name:
        return str(name)
    return "chat_model"


_PROVIDER_FROM_MODULE: Dict[str, str] = {
    "langchain_openai": "openai",
    "langchain_anthropic": "anthropic",
    "langchain_google_genai": "google",
    "langchain_google_vertexai": "google",
    "langchain_groq": "groq",
    "langchain_cohere": "cohere",
    "langchain_mistralai": "mistral",
    "langchain_fireworks": "fireworks",
    "langchain_together": "together",
    "langchain_aws": "bedrock",
    "langchain_nvidia_ai_endpoints": "nvidia",
    "langchain_ollama": "ollama",
    "langchain_huggingface": "huggingface",
    "langchain_perplexity": "perplexity",
    "langchain_xai": "xai",
}

_PROVIDER_FROM_CLASS: Dict[str, str] = {
    "chatopenai": "openai",
    "azurechatopenai": "openai",
    "openai": "openai",
    "chatanthropic": "anthropic",
    "anthropic": "anthropic",
    "chatgoogleGenerativeai": "google",
    "chatvertexai": "google",
    "chatgroq": "groq",
    "chatcohere": "cohere",
    "chatmistralai": "mistral",
    "chatfireworks": "fireworks",
    "chattogether": "together",
    "chatbedrock": "bedrock",
    "chatnvidia": "nvidia",
    "chatollama": "ollama",
    "chatperplexity": "perplexity",
}


def _infer_provider_from_serialized(serialized: Dict[str, Any]) -> str:
    """Infer the real LLM provider from LangChain's serialized model dict."""
    # id is a list like ["langchain_openai", "chat_models", "ChatOpenAI"]
    id_parts = serialized.get("id") or []
    if isinstance(id_parts, list):
        for part in id_parts:
            p = str(part or "").lower()
            for mod, provider in _PROVIDER_FROM_MODULE.items():
                if p == mod or p.startswith(mod + "."):
                    return provider
    # Fall back to class name
    name = str(serialized.get("name") or "").lower().replace("_", "").replace("-", "")
    for cls, provider in _PROVIDER_FROM_CLASS.items():
        if name == cls.lower() or name.startswith(cls.lower()):
            return provider
    return "langchain"


def _fill_llm_span_from_llm_result(s: Span, response: Any) -> None:
    """Populate span fields from LangChain ``LLMResult``."""
    try:
        gens = response.generations
    except Exception:
        return
    if not gens or not gens[0]:
        return
    gen0 = gens[0][0]
    msg = getattr(gen0, "message", None)
    if msg is not None:
        content = getattr(msg, "content", "") or ""
        if isinstance(content, list):
            content = json.dumps(content, default=str)
        s.assistant_preview = str(content)[:2000]
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            s.had_tool_call = True
            names = []
            for tc in tcs:
                if isinstance(tc, dict):
                    names.append({"name": tc.get("name"), "args": tc.get("args")})
                else:
                    names.append({"name": getattr(tc, "name", None), "args": getattr(tc, "args", None)})
            s.tool_call_summary = json.dumps(names, default=str)[:2000]
        inp_t, out_t, cache_read = 0, 0, 0
        rm = getattr(msg, "response_metadata", None) or {}
        if isinstance(rm, dict):
            u = rm.get("token_usage") or rm.get("usage") or {}
            if isinstance(u, dict):
                inp_t = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
                out_t = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
                ptd = u.get("prompt_tokens_details")
                if isinstance(ptd, dict):
                    try:
                        cache_read = int(ptd.get("cached_tokens") or 0)
                    except (TypeError, ValueError):
                        cache_read = 0
        # LangChain chat models often attach counts only on ``usage_metadata`` (not ``token_usage``).
        um = getattr(msg, "usage_metadata", None)
        if isinstance(um, dict):
            if inp_t == 0:
                try:
                    inp_t = int(um.get("input_tokens") or 0)
                except (TypeError, ValueError):
                    pass
            if out_t == 0:
                try:
                    out_t = int(um.get("output_tokens") or 0)
                except (TypeError, ValueError):
                    pass
            det = um.get("input_token_details") or {}
            if isinstance(det, dict):
                try:
                    cr = int(det.get("cache_read") or 0)
                except (TypeError, ValueError):
                    cr = 0
                if cr > 0:
                    cache_read = max(cache_read, cr)
        s.input_tokens = inp_t
        s.output_tokens = out_t
        if inp_t > 0 and cache_read > 0:
            s.cache_read_tokens = max(0, min(cache_read, inp_t))
        return
    text = getattr(gen0, "text", None) or ""
    s.assistant_preview = str(text)[:2000]


def _tool_output_str(output: Any) -> str:
    if output is None:
        return ""
    c = getattr(output, "content", None)
    if c is not None:
        return _safe_str(c, 4000)
    return _safe_str(output, 4000)


# Metadata keys for trace_lane (``langgraph_node`` is promoted to ``Span.agent_name`` / metadata.agent_name).
_LANE_METADATA_KEYS: Tuple[str, ...] = (
    "langgraph_path",
    "langgraph_checkpoint_ns",
    "checkpoint_ns",
    "ls_run_name",
    "ls_name",
    "run_name",
)

_UUID_LIKE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ``create_react_agent`` / inner compiled graphs often report ``langgraph_node`` as these while
# the outer subgraph name lives on ``ls_run_name`` (``name=`` on the compiled agent).
_GENERIC_LANGGRAPH_NODES: frozenset[str] = frozenset(
    {
        "agent",
        "tools",
        "tool",
        "model",
        "models",
        "llm",
        "chat",
        "retriever",
        "__start__",
        "__end__",
        "call_model",
    }
)

# When ``langgraph_node`` is present but generic, prefer these (LangSmith / Runnable ``name=``).
_AGENT_NAME_FALLBACK_METADATA_KEYS: Tuple[str, ...] = (
    "ls_run_name",
    "ls_name",
    "run_name",
)


def _clip_metadata_label(s: str, max_len: int) -> str:
    s = s.strip()
    if not s:
        return ""
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _is_generic_langgraph_node(name: str) -> bool:
    return name.strip().lower() in _GENERIC_LANGGRAPH_NODES


def _agent_name_from_fallback_metadata(metadata: Dict[str, Any], *, max_len: int) -> Optional[str]:
    for key in _AGENT_NAME_FALLBACK_METADATA_KEYS:
        v = metadata.get(key)
        if v is None:
            continue
        out = _clip_metadata_label(str(v), max_len)
        if out:
            return out
    return None


def _agent_name_from_langgraph_metadata(
    metadata: Optional[Dict[str, Any]],
    *,
    max_len: int = 128,
) -> Optional[str]:
    """
    Derive ``metadata.agent_name`` from LangGraph / LangChain callback metadata.

    Prefer a **specific** ``langgraph_node``. Inner ReAct graphs often set it to generic values
    (``agent``, ``tools``) while the real subgraph name is on ``ls_run_name`` (e.g. ``research_expert``);
    in that case we use the fallback keys instead. If ``langgraph_node`` is missing, we do **not**
    invent an agent name from fallbacks (``trace_lane`` still uses those keys).
    """
    if max_len < 1:
        max_len = 128
    if not isinstance(metadata, dict):
        return None
    raw_node = metadata.get("langgraph_node")
    if raw_node is None:
        return None
    node_s = str(raw_node).strip()
    if not node_s:
        return None
    if not _is_generic_langgraph_node(node_s):
        out = _clip_metadata_label(node_s, max_len)
        return out or None
    return _agent_name_from_fallback_metadata(metadata, max_len=max_len)


_PREGEL_PULL_RE = re.compile(r"__pregel_pull['\"]?\s*,\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


def _normalize_trace_lane_for_storage(raw: Optional[str], *, max_len: int = 128) -> Optional[str]:
    """
    Collapse LangGraph ``('__pregel_pull', 'node')`` tuple strings to ``node`` for stored ``metadata.trace_lane``.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if "__pregel_push" in s:
        return None
    m = _PREGEL_PULL_RE.search(s)
    if m:
        inner = str(m.group(1) or "").strip()
        if inner.lower() == "agent":
            return None
        return inner[:max_len] if inner else None
    return s[:max_len] if len(s) > max_len else s


def _trace_lane_from_langchain(
    tags: Optional[List[str]],
    metadata: Optional[Dict[str, Any]],
    *,
    max_len: int = 128,
) -> Optional[str]:
    """
    Best-effort graph / runnable label for dashboard ``metadata.trace_lane``.

    Prefer structured ``metadata`` (LangGraph path / checkpoint ns, LangSmith run name), then
    the first non-empty callback ``tag``. ``langgraph_node`` is handled separately as
    ``agent_name`` (see ``_agent_name_from_langgraph_metadata``). Does not read prompts or bodies.
    """
    if max_len < 1:
        max_len = 128

    if isinstance(metadata, dict):
        for key in _LANE_METADATA_KEYS:
            v = metadata.get(key)
            if v is None:
                continue
            out = _clip_metadata_label(str(v), max_len)
            if out:
                return out

    if tags:
        for raw in tags:
            t = str(raw).strip() if raw is not None else ""
            if not t or len(t) > 256:
                continue
            if _UUID_LIKE.match(t):
                continue
            if t.lower().startswith("http://") or t.lower().startswith("https://"):
                continue
            out = _clip_metadata_label(t, max_len)
            if out:
                return out
    return None


class MiniObserveCallbackHandler(BaseCallbackHandler):
    """
    Maps LangChain chat/LLM and tool callbacks to Tracer ``llm`` / ``tool`` spans.

    Pass ``root_parent_span_id`` from the root agent span when using ``traced_agent_session``.
    LangChain ``parent_run_id`` is resolved to the parent span's ``client_span_id`` when that
    parent run was also traced by this handler. When ``parent_run_id`` is missing or not
    mapped, the handler falls back to the last completed LLM/tool span so batches still
    carry ``parent_client_span_id`` (dashboard tree).

    Runnable ``tags`` / ``metadata`` populate ``Span.trace_lane`` when recognizable (see
    ``_trace_lane_from_langchain``). ``Span.agent_name`` comes from ``langgraph_node`` when it is a
    meaningful name; generic inner nodes (``agent``, ``tools``, ``__start__``, …) use
    ``ls_run_name`` / ``ls_name`` / ``run_name`` instead so compiled sub-agents group by subgraph
    name without client changes.
    """

    def __init__(self, tracer: Tracer, *, root_parent_span_id: Optional[str] = None) -> None:
        super().__init__()
        self._tracer = tracer
        self._root_parent_span_id = root_parent_span_id
        self._lc_run_to_span_id: Dict[str, str] = {}
        self._pending_llm: Dict[str, Tuple[Any, Span]] = {}
        self._pending_tool: Dict[str, Tuple[Any, Span]] = {}
        # When LangChain omits parent_run_id or it is not yet mapped, link spans in callback order:
        # LLM → tool → LLM → … (see _parent_for_llm_start / _parent_for_tool_start).
        self._last_completed_llm_span_id: Optional[str] = None
        self._last_completed_tool_span_id: Optional[str] = None

    def _mapped_lc_parent_span(self, parent_run_id: Optional["UUID"]) -> Optional[str]:
        """Return span id for parent_run_id if this handler registered that run; else None."""
        if parent_run_id is None:
            return None
        return self._lc_run_to_span_id.get(str(parent_run_id))

    def _parent_for_llm_start(self, parent_run_id: Optional["UUID"]) -> Optional[str]:
        mapped = self._mapped_lc_parent_span(parent_run_id)
        if mapped is not None:
            parent = mapped
        elif parent_run_id is not None:
            # LangChain parent run we do not trace (graph / sequence id) — same as before: root.
            parent = self._root_parent_span_id
        elif self._last_completed_tool_span_id is not None:
            parent = self._last_completed_tool_span_id
        else:
            parent = self._root_parent_span_id
        # Any new LLM span consumes or supersedes the “after tool” hint for the next LLM.
        self._last_completed_tool_span_id = None
        return parent

    def _parent_for_tool_start(self, parent_run_id: Optional["UUID"]) -> Optional[str]:
        mapped = self._mapped_lc_parent_span(parent_run_id)
        if mapped is not None:
            return mapped
        if parent_run_id is not None:
            return self._root_parent_span_id
        if self._last_completed_llm_span_id is not None:
            return self._last_completed_llm_span_id
        return self._root_parent_span_id

    def _register_run(self, run_id: Any, span_id: str) -> None:
        self._lc_run_to_span_id[str(run_id)] = span_id

    def _exit_llm(self, run_id: Any, *, exc_type=None, exc_val=None, exc_tb=None) -> None:
        key = str(run_id)
        pair = self._pending_llm.pop(key, None)
        if not pair:
            return
        ctx, _s = pair
        ctx.__exit__(exc_type, exc_val, exc_tb)

    def _exit_tool(self, run_id: Any, *, exc_type=None, exc_val=None, exc_tb=None) -> None:
        key = str(run_id)
        pair = self._pending_tool.pop(key, None)
        if not pair:
            return
        ctx, _s = pair
        ctx.__exit__(exc_type, exc_val, exc_tb)

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        *,
        run_id: "UUID",
        parent_run_id: Optional["UUID"] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        if key in self._pending_llm:
            return
        flat = _flatten_chat_batches(messages)
        name = _serialized_model_label(serialized)
        parent = self._parent_for_llm_start(parent_run_id)
        ctx = self._tracer.span("llm", name, parent_id=parent)
        s = ctx.__enter__()
        s.model = name
        s.provider = _infer_provider_from_serialized(serialized)
        ag = _agent_name_from_langgraph_metadata(metadata)
        if ag:
            s.agent_name = ag
        lane = _normalize_trace_lane_for_storage(_trace_lane_from_langchain(tags, metadata))
        if lane:
            s.trace_lane = lane
        dicts = _lc_messages_to_dicts(flat)
        s.request_messages = strip_messages_for_log(dicts)
        if dicts:
            s.prompt_fingerprint = s.fingerprint_prompt(dicts)
            sys_c = next((m.get("content") for m in dicts if m.get("role") == "system"), "")
            s.system_prompt_preview = str(sys_c or "")[:500]
        self._pending_llm[key] = (ctx, s)
        self._register_run(run_id, s.span_id)

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: "UUID",
        parent_run_id: Optional["UUID"] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        if key in self._pending_llm:
            return
        name = str(serialized.get("name") or serialized.get("id") or "llm")
        parent = self._parent_for_llm_start(parent_run_id)
        ctx = self._tracer.span("llm", name, parent_id=parent)
        s = ctx.__enter__()
        s.model = name
        s.provider = _infer_provider_from_serialized(serialized)
        ag = _agent_name_from_langgraph_metadata(metadata)
        if ag:
            s.agent_name = ag
        lane = _normalize_trace_lane_for_storage(_trace_lane_from_langchain(tags, metadata))
        if lane:
            s.trace_lane = lane
        msgs = [{"role": "user", "content": p} for p in prompts] if prompts else []
        s.request_messages = strip_messages_for_log(msgs) if msgs else None
        if msgs:
            s.prompt_fingerprint = s.fingerprint_prompt(msgs)
        self._pending_llm[key] = (ctx, s)
        self._register_run(run_id, s.span_id)

    def on_llm_end(self, response: Any, *, run_id: "UUID", **kwargs: Any) -> None:
        key = str(run_id)
        pair = self._pending_llm.get(key)
        if not pair:
            return
        _ctx, s = pair
        try:
            _fill_llm_span_from_llm_result(s, response)
        except Exception:
            pass
        self._last_completed_llm_span_id = s.span_id
        self._exit_llm(run_id, exc_type=None, exc_val=None, exc_tb=None)

    def on_llm_error(self, error: BaseException, *, run_id: "UUID", **kwargs: Any) -> None:
        key = str(run_id)
        pair = self._pending_llm.get(key)
        if not pair:
            return
        _ctx, s = pair
        s.error = _safe_str(error, 2000)
        self._last_completed_llm_span_id = s.span_id
        self._exit_llm(run_id, exc_type=type(error), exc_val=error, exc_tb=getattr(error, "__traceback__", None))

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: "UUID",
        parent_run_id: Optional["UUID"] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        if key in self._pending_tool:
            return
        tool_name = str(serialized.get("name") or "tool")
        safe_name = re.sub(r"[^\w.\-]+", "_", tool_name)[:64] or "tool"
        parent = self._parent_for_tool_start(parent_run_id)
        ctx = self._tracer.span("tool", safe_name, parent_id=parent)
        s = ctx.__enter__()
        s.tool_name = tool_name
        s.tool_args = _tool_args_from_inputs(serialized, input_str, inputs)
        ag = _agent_name_from_langgraph_metadata(metadata)
        if ag:
            s.agent_name = ag
        lane = _normalize_trace_lane_for_storage(_trace_lane_from_langchain(tags, metadata))
        if lane:
            s.trace_lane = lane
        self._pending_tool[key] = (ctx, s)
        self._register_run(run_id, s.span_id)

    def on_tool_end(self, output: Any, *, run_id: "UUID", **kwargs: Any) -> None:
        key = str(run_id)
        pair = self._pending_tool.get(key)
        if not pair:
            return
        _ctx, s = pair
        s.tool_result = _tool_output_str(output)
        self._last_completed_tool_span_id = s.span_id
        self._exit_tool(run_id, exc_type=None, exc_val=None, exc_tb=None)

    def on_tool_error(self, error: BaseException, *, run_id: "UUID", **kwargs: Any) -> None:
        key = str(run_id)
        pair = self._pending_tool.get(key)
        if not pair:
            return
        _ctx, s = pair
        s.error = _safe_str(error, 2000)
        self._last_completed_tool_span_id = s.span_id
        self._exit_tool(run_id, exc_type=type(error), exc_val=error, exc_tb=getattr(error, "__traceback__", None))
