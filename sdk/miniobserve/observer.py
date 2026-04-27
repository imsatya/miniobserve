"""Core observer logic for MiniObserve."""
import atexit
import json
import os
import queue
import threading
import time
import uuid
import functools
import inspect
import httpx
from contextvars import ContextVar
from typing import Optional, Callable, Any, Dict
from datetime import datetime, timezone

from .env_url import resolve_miniobserve_http_base_url
from .http_transport import request_json

_instance: Optional["MiniObserve"] = None

run_id_cv: ContextVar[Optional[str]] = ContextVar("miniobserve_run_id", default=None)
span_stack_cv: ContextVar[Optional[list]] = ContextVar("miniobserve_span_stack", default=None)


def _span_stack() -> list:
    s = span_stack_cv.get()
    if s is None:
        s = []
        span_stack_cv.set(s)
    return s


def _openai_message_response_text(message: Any) -> str:
    """Assistant message: text content, else JSON of tool_calls (tool-only turns)."""
    if message is None:
        return ""
    raw = getattr(message, "content", None)
    if isinstance(raw, str) and raw.strip():
        return raw[:4000]
    if isinstance(raw, list) and raw:
        try:
            return json.dumps(raw)[:4000]
        except Exception:
            return str(raw)[:4000]
    tc = getattr(message, "tool_calls", None)
    if tc:
        try:
            out = []
            for t in tc:
                if hasattr(t, "model_dump"):
                    out.append(t.model_dump())
                elif isinstance(t, dict):
                    out.append(t)
                else:
                    out.append(str(t))
            return json.dumps(out)[:4000]
        except Exception:
            return str(tc)[:4000]
    return ""


def _cached_tokens_from_usage(usage) -> int:
    """OpenAI usage: prompt_tokens_details.cached_tokens."""
    if usage is None:
        return 0
    try:
        ptd = getattr(usage, "prompt_tokens_details", None)
        if ptd is not None:
            v = getattr(ptd, "cached_tokens", None)
            if v is not None:
                return max(0, int(v))
            if isinstance(ptd, dict):
                return max(0, int(ptd.get("cached_tokens") or 0))
        if hasattr(usage, "model_dump"):
            d = usage.model_dump()
            ptd = d.get("prompt_tokens_details") or {}
            if isinstance(ptd, dict):
                return max(0, int(ptd.get("cached_tokens") or 0))
    except Exception:
        pass
    return 0


def _extract_llm_fields(result: Any, kwargs: dict, default_model: str, default_provider: str) -> Dict[str, Any]:
    _model = default_model
    _provider = default_provider
    prompt_text = ""
    response_text = ""
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0

    messages = kwargs.get("messages", [])
    if messages and isinstance(messages, list):
        prompt_text = " | ".join(
            m.get("content", "") for m in messages if isinstance(m, dict)
        )

    if result is not None:
        try:
            if hasattr(result, "model"):
                _model = result.model or _model
            if hasattr(result, "usage") and result.usage:
                input_tokens = getattr(result.usage, "prompt_tokens", 0) or getattr(result.usage, "input_tokens", 0)
                output_tokens = getattr(result.usage, "completion_tokens", 0) or getattr(result.usage, "output_tokens", 0)
                cached_input_tokens = _cached_tokens_from_usage(result.usage)
            if hasattr(result, "choices") and result.choices:
                response_text = _openai_message_response_text(result.choices[0].message)
            elif hasattr(result, "content") and isinstance(result.content, list):
                response_text = " ".join(
                    getattr(b, "text", "") for b in result.content
                )
        except Exception:
            pass

    inp = int(input_tokens or 0)
    cached_input_tokens = max(0, min(cached_input_tokens, inp))
    return {
        "model": _model,
        "provider": _provider,
        "prompt": prompt_text,
        "response": response_text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
    }


class MiniObserve:
    def __init__(
        self,
        server_url: Optional[str] = None,
        app_name: str = "default",
        api_key: Optional[str] = None,
        *,
        max_retries: Optional[int] = None,
    ):
        if server_url is None:
            resolved = resolve_miniobserve_http_base_url()
            server_url = resolved if resolved is not None else "http://localhost:7823"
        self.server_url = str(server_url).strip().rstrip("/") or "http://localhost:7823"
        self.app_name = app_name
        self.api_key = api_key
        mr = max_retries
        if mr is None:
            try:
                mr = int((os.getenv("MINIOBSERVE_HTTP_MAX_RETRIES") or "4").strip())
            except ValueError:
                mr = 4
        self._max_retries = max(0, int(mr))
        self._client = httpx.Client(timeout=30.0)
        self._bg_queue: Optional[queue.Queue] = None
        self._bg_thread: Optional[threading.Thread] = None
        if (os.getenv("MINIOBSERVE_BACKGROUND_FLUSH") or "").strip().lower() in ("1", "true", "yes", "on"):
            self._bg_queue = queue.Queue(4000)
            self._bg_thread = threading.Thread(
                target=self._background_flush_loop,
                name="miniobserve-bg-flush",
                daemon=True,
            )
            self._bg_thread.start()
            atexit.register(self._drain_background_queue_at_exit)

    def _background_flush_loop(self) -> None:
        q = self._bg_queue
        if q is None:
            return
        while True:
            payload = q.get()
            if payload is None:
                break
            request_json(
                self._client,
                "POST",
                f"{self.server_url}/api/log",
                json_body=payload,
                headers=self._headers(),
                max_retries=self._max_retries,
            )

    def _drain_background_queue_at_exit(self) -> None:
        if self._bg_queue is not None:
            try:
                self._bg_queue.put_nowait(None)
            except queue.Full:
                pass
        if self._bg_thread is not None:
            self._bg_thread.join(timeout=12.0)
        q = self._bg_queue
        if q is None:
            return
        while True:
            try:
                payload = q.get_nowait()
            except queue.Empty:
                break
            if payload:
                request_json(
                    self._client,
                    "POST",
                    f"{self.server_url}/api/log",
                    json_body=payload,
                    headers=self._headers(),
                    max_retries=self._max_retries,
                )

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        rep = (os.getenv("MINIOBSERVE_CLIENT") or "").strip()
        if rep:
            h["X-MiniObserve-Reporter"] = rep
        return h

    def _post_json(self, payload: dict) -> Optional[dict]:
        _, data = request_json(
            self._client,
            "POST",
            f"{self.server_url}/api/log",
            json_body=payload,
            headers=self._headers(),
            max_retries=self._max_retries,
        )
        return data

    def _patch_json(self, payload: dict) -> Optional[dict]:
        _, data = request_json(
            self._client,
            "PATCH",
            f"{self.server_url}/api/log",
            json_body=payload,
            headers=self._headers(),
            max_retries=self._max_retries,
        )
        return data

    def post_logs_batch(self, logs: list[dict], *, run_id: Optional[str] = None) -> Optional[dict]:
        """POST /api/logs — ordered spans with optional client_span_id / parent_client_span_id."""
        h = self._headers()
        if run_id:
            h["X-MiniObserve-Run-Id"] = str(run_id).strip()
        _, data = request_json(
            self._client,
            "POST",
            f"{self.server_url}/api/logs",
            json_body={"logs": logs},
            headers=h,
            max_retries=self._max_retries,
        )
        return data

    def log(
        self,
        *,
        model: str,
        provider: str,
        prompt: str,
        response: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0,
        metadata: Optional[dict] = None,
        error: Optional[str] = None,
        run_id: Optional[str] = None,
        span_name: Optional[str] = None,
        parent_span_id: Optional[int] = None,
        cached_input_tokens: int = 0,
        span_type: Optional[str] = None,
        client_span_id: Optional[str] = None,
        parent_client_span_id: Optional[str] = None,
    ):
        payload = {
            "app_name": self.app_name,
            "model": model,
            "provider": provider,
            "prompt": prompt,
            "response": response,
            "input_tokens": input_tokens,
            "cached_input_tokens": max(0, min(int(cached_input_tokens or 0), int(input_tokens or 0))),
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_ms": latency_ms,
            "cost_usd": 0.0,
            "error": error,
            "run_id": run_id,
            "span_name": span_name,
            "parent_span_id": parent_span_id,
            "span_type": span_type,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if client_span_id:
            payload["client_span_id"] = str(client_span_id).strip()
        if parent_client_span_id:
            payload["parent_client_span_id"] = str(parent_client_span_id).strip()
        if self._bg_queue is not None:
            try:
                self._bg_queue.put_nowait(dict(payload))
                return payload
            except queue.Full:
                pass
        resp = self._post_json(payload)
        if resp and resp.get("id") is not None:
            payload["id"] = resp["id"]
        return payload

    def log_tool(
        self,
        name: str,
        args: Any = None,
        result: Any = None,
        *,
        latency_ms: float = 0,
        error: Optional[str] = None,
    ):
        """Record a tool invocation as a span (span_type=tool). Uses active trace context when set."""
        md: dict = {"tool_name": name}
        try:
            md["tool_args"] = (json.dumps(args, default=str) if args is not None else "")[:4000]
        except Exception:
            md["tool_args"] = str(args)[:4000]
        try:
            md["tool_result"] = (json.dumps(result, default=str) if result is not None else "")[:4000]
        except Exception:
            md["tool_result"] = str(result)[:4000]
        tid = run_id_cv.get()
        stack = _span_stack()
        parent = stack[-1] if stack else None
        model_name = (name or "tool")[:200]
        return self.log(
            model=model_name,
            provider="tool",
            prompt=md.get("tool_args") or "",
            response=md.get("tool_result") or "",
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=error,
            run_id=tid,
            span_name=name,
            parent_span_id=parent,
            span_type="tool",
            metadata=md,
        )

    def _begin_span(
        self,
        *,
        run_id: str,
        parent_span_id: Optional[int],
        span_name: str,
        model: str,
        provider: str,
        span_type: str,
    ) -> Optional[int]:
        payload = {
            "app_name": self.app_name,
            "model": model,
            "provider": provider,
            "prompt": "",
            "response": "",
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0.0,
            "cost_usd": 0.0,
            "error": None,
            "run_id": run_id,
            "span_name": span_name,
            "parent_span_id": parent_span_id,
            "span_type": span_type,
            "metadata": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data = self._post_json(payload)
        if not data or data.get("id") is None:
            return None
        return int(data["id"])

    def _complete_span(self, log_id: int, fields: dict) -> None:
        patch = {"id": log_id, **fields}
        inp = int(patch.get("input_tokens") or 0)
        if "cached_input_tokens" in patch:
            c = int(patch.get("cached_input_tokens") or 0)
            patch["cached_input_tokens"] = max(0, min(c, inp))
        self._patch_json(patch)


def init(
    server_url: Optional[str] = None,
    app_name: str = "default",
    api_key: Optional[str] = None,
) -> MiniObserve:
    global _instance
    _instance = MiniObserve(server_url=server_url, app_name=app_name, api_key=api_key)
    return _instance


def _get_instance() -> MiniObserve:
    global _instance
    if _instance is None:
        _instance = MiniObserve()
    return _instance


def log_tool(
    name: str,
    args: Any = None,
    result: Any = None,
    **kwargs,
):
    """Log a tool call using the global MiniObserve instance (call init() first)."""
    return _get_instance().log_tool(name, args, result, **kwargs)


def observe(
    func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    provider: str = "unknown",
    model: str = "unknown",
    span_type: str = "llm",
):
    """
    Decorator to observe OpenAI / Anthropic SDK calls with run_id + parent_span_id propagation.

    Opens a span at call start (POST /api/log) and completes it in finally (PATCH /api/log).
    Nested decorated calls automatically link to the parent span.
    """
    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__name__

        def run_observed_sync(*args, **kwargs):
            obs = _get_instance()
            stack = _span_stack()
            tid = run_id_cv.get()
            if tid is None:
                tid = str(uuid.uuid4())
                run_id_cv.set(tid)
            parent_span_id = stack[-1] if stack else None
            rid = obs._begin_span(
                run_id=tid,
                parent_span_id=parent_span_id,
                span_name=span_name,
                model=model,
                provider=provider,
                span_type=span_type,
            )
            if rid is not None:
                stack.append(rid)
            t0 = time.perf_counter()
            error_msg = None
            result = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                latency_ms = (time.perf_counter() - t0) * 1000
                ex = _extract_llm_fields(result, kwargs, model, provider)
                inp = int(ex["input_tokens"] or 0)
                cost = 0.0
                try:
                    if rid is not None:
                        obs._complete_span(
                            rid,
                            {
                                "model": ex["model"],
                                "provider": ex["provider"],
                                "prompt": (ex["prompt"] or "")[:4000],
                                "response": (ex["response"] or "")[:4000],
                                "input_tokens": ex["input_tokens"],
                                "output_tokens": ex["output_tokens"],
                                "cached_input_tokens": ex["cached_input_tokens"],
                                "total_tokens": inp + int(ex["output_tokens"] or 0),
                                "latency_ms": latency_ms,
                                "cost_usd": cost,
                                "error": error_msg,
                            },
                        )
                    else:
                        obs.log(
                            model=ex["model"],
                            provider=ex["provider"],
                            prompt=(ex["prompt"] or "")[:4000],
                            response=(ex["response"] or "")[:4000],
                            input_tokens=ex["input_tokens"],
                            output_tokens=ex["output_tokens"],
                            cached_input_tokens=ex["cached_input_tokens"],
                            latency_ms=latency_ms,
                            error=error_msg,
                            run_id=tid,
                            span_name=span_name,
                            parent_span_id=parent_span_id,
                            span_type=span_type,
                        )
                finally:
                    if rid is not None and stack and stack[-1] == rid:
                        stack.pop()
                    if not stack:
                        run_id_cv.set(None)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return run_observed_sync(*args, **kwargs)

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            obs = _get_instance()
            stack = _span_stack()
            tid = run_id_cv.get()
            if tid is None:
                tid = str(uuid.uuid4())
                run_id_cv.set(tid)
            parent_span_id = stack[-1] if stack else None
            rid = obs._begin_span(
                run_id=tid,
                parent_span_id=parent_span_id,
                span_name=span_name,
                model=model,
                provider=provider,
                span_type=span_type,
            )
            if rid is not None:
                stack.append(rid)
            t0 = time.perf_counter()
            error_msg = None
            result = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as e:
                error_msg = str(e)
                raise
            finally:
                latency_ms = (time.perf_counter() - t0) * 1000
                ex = _extract_llm_fields(result, kwargs, model, provider)
                inp = int(ex["input_tokens"] or 0)
                cost = 0.0
                try:
                    if rid is not None:
                        obs._complete_span(
                            rid,
                            {
                                "model": ex["model"],
                                "provider": ex["provider"],
                                "prompt": (ex["prompt"] or "")[:4000],
                                "response": (ex["response"] or "")[:4000],
                                "input_tokens": ex["input_tokens"],
                                "output_tokens": ex["output_tokens"],
                                "cached_input_tokens": ex["cached_input_tokens"],
                                "total_tokens": inp + int(ex["output_tokens"] or 0),
                                "latency_ms": latency_ms,
                                "cost_usd": cost,
                                "error": error_msg,
                            },
                        )
                    else:
                        obs.log(
                            model=ex["model"],
                            provider=ex["provider"],
                            prompt=(ex["prompt"] or "")[:4000],
                            response=(ex["response"] or "")[:4000],
                            input_tokens=ex["input_tokens"],
                            output_tokens=ex["output_tokens"],
                            cached_input_tokens=ex["cached_input_tokens"],
                            latency_ms=latency_ms,
                            error=error_msg,
                            run_id=tid,
                            span_name=span_name,
                            parent_span_id=parent_span_id,
                            span_type=span_type,
                        )
                finally:
                    if rid is not None and stack and stack[-1] == rid:
                        stack.pop()
                    if not stack:
                        run_id_cv.set(None)

        if inspect.iscoroutinefunction(fn):
            return async_wrapper
        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def trace(func: Optional[Callable] = None, **kwargs):
    """Like observe(), but defaults span_type to \"agent\" (for orchestration / agent entrypoints)."""
    kwargs.setdefault("span_type", "agent")
    return observe(func, **kwargs)
