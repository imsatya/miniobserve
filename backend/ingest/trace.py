"""Log ingest parsing/normalization helpers used by API routes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError, field_validator

# Stored on each row so later spans can resolve parent_client_span_id via DB lookup.
MINIOBSERVE_CLIENT_SPAN_META_KEY = "miniobserve_client_span_id"


def pop_client_span_correlation(body: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    Remove optional client-owned span keys before LogEntry validation.
    Returns (client_span_id, parent_client_span_id) as non-empty strings or None.
    """
    raw_c = body.pop("client_span_id", None)
    raw_p = body.pop("parent_client_span_id", None)
    cid = str(raw_c).strip() if raw_c is not None and str(raw_c).strip() else None
    pcid = str(raw_p).strip() if raw_p is not None and str(raw_p).strip() else None
    return cid, pcid


def attach_client_span_metadata(row: dict, client_span_id: Optional[str]) -> None:
    if not client_span_id:
        return
    md = row.get("metadata")
    if not isinstance(md, dict):
        md = {}
    md = {**md, MINIOBSERVE_CLIENT_SPAN_META_KEY: client_span_id}
    row["metadata"] = md


class LogEntry(BaseModel):
    app_name: str = "default"
    model: str = "unknown"
    provider: str = "unknown"
    prompt: str = ""
    messages: Optional[list] = None  # structured [{role, content}, ...]; also stored in messages column
    response: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0
    cost_usd: float = 0
    error: Optional[str] = None
    run_id: Optional[str] = None
    span_name: Optional[str] = None
    parent_span_id: Optional[int] = None
    span_type: Optional[str] = None
    metadata: dict = {}
    timestamp: Optional[str] = None

    @field_validator("model", "provider", mode="before")
    @classmethod
    def _model_provider_non_empty(cls, v: Any) -> str:
        if v is None:
            return "unknown"
        s = str(v).strip()
        return s if s else "unknown"


def sanitize_model_provider(row: dict) -> None:
    row["model"] = str(row.get("model") or "").strip() or "unknown"
    row["provider"] = str(row.get("provider") or "").strip() or "unknown"


def ingest_db_exception_detail(exc: BaseException) -> Any:
    try:
        from postgrest.exceptions import APIError as PostgrestAPIError
    except ImportError:  # pragma: no cover
        PostgrestAPIError = None  # type: ignore[misc, assignment]
    if PostgrestAPIError is not None and isinstance(exc, PostgrestAPIError):
        out: dict[str, Any] = dict(exc.json())
        msg = (out.get("message") or "").lower()
        if out.get("code") == "42501" or "row-level security" in msg:
            out["remediation"] = (
                "Set SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY to the service_role secret) on the "
                "MiniObserve backend, or run backend/supabase_fix_llm_logs_rls.sql in the Supabase SQL editor "
                "(disables RLS on mo_llm_logs / mo_run_summaries)."
            )
        return out
    return str(exc) or type(exc).__name__


def _merge_span_timestamps_into_metadata(row: dict, body: dict) -> None:
    """Optional ISO8601 span bounds from POST body → stored under metadata (no DB columns)."""
    changed = False
    md = row.get("metadata")
    if not isinstance(md, dict):
        md = {}
    else:
        md = dict(md)
    for k in ("started_at", "ended_at"):
        if k not in body:
            continue
        raw = body.get(k)
        if raw is None:
            if k in md:
                del md[k]
                changed = True
            continue
        s = str(raw).strip()
        if not s:
            if k in md:
                del md[k]
                changed = True
            continue
        md[k] = s[:128]
        changed = True
    if changed:
        row["metadata"] = md


def merge_patch_span_timestamps_from_body(body: dict, existing_row: dict, updates: dict) -> None:
    """
    If PATCH body includes started_at / ended_at (top-level), merge them into metadata
    together with any existing row metadata and optional updates['metadata'].
    """
    if not any(k in body for k in ("started_at", "ended_at")):
        return
    em = existing_row.get("metadata") or {}
    if isinstance(em, str):
        try:
            em = json.loads(em) if em.strip() else {}
        except Exception:
            em = {}
    if not isinstance(em, dict):
        em = {}
    md = dict(em)
    if "metadata" in updates and isinstance(updates["metadata"], dict):
        md.update(updates["metadata"])
    for k in ("started_at", "ended_at"):
        if k not in body:
            continue
        v = body[k]
        if v is None:
            md.pop(k, None)
            continue
        s = str(v).strip()
        if s:
            md[k] = s[:128]
        else:
            md.pop(k, None)
    updates["metadata"] = md


def promote_metadata_span_type_to_row(row: dict) -> None:
    """Copy ``metadata.span_type`` to top-level ``span_type`` when the column is unset (Tracer batch)."""
    cur = row.get("span_type")
    if isinstance(cur, str) and cur.strip():
        row["span_type"] = cur.strip().lower()
        return
    md = row.get("metadata")
    if not isinstance(md, dict):
        return
    st = md.get("span_type")
    if isinstance(st, str) and st.strip():
        row["span_type"] = st.strip().lower()


def promote_metadata_span_type_for_patch(existing: dict, updates: dict) -> None:
    """If patch does not set ``span_type`` but merged metadata has ``span_type``, add to ``updates``."""
    em = existing.get("metadata")
    if isinstance(em, dict):
        merged = dict(em)
    else:
        parsed = _json_object_if_string(em)
        merged = dict(parsed) if isinstance(parsed, dict) else {}
    if "metadata" in updates and isinstance(updates["metadata"], dict):
        merged.update(updates["metadata"])
    cur = (updates.get("span_type") if "span_type" in updates else None)
    if cur is None:
        cur = existing.get("span_type")
    if isinstance(cur, str) and cur.strip():
        return
    st = merged.get("span_type") if isinstance(merged, dict) else None
    if isinstance(st, str) and st.strip():
        updates["span_type"] = st.strip().lower()


def ensure_log_row_for_db(row: dict) -> None:
    row.setdefault("prompt", "")
    row.setdefault("response", "")
    row.setdefault("input_tokens", 0)
    row.setdefault("output_tokens", 0)
    row.setdefault("cached_input_tokens", 0)
    row.setdefault("latency_ms", 0.0)
    row.setdefault("cost_usd", 0.0)
    row.setdefault("error", None)
    row.setdefault("run_id", None)
    row.setdefault("span_name", None)
    if not row.get("timestamp"):
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        tin = int(row.get("input_tokens") or 0)
        tout = int(row.get("output_tokens") or 0)
    except (TypeError, ValueError):
        tin, tout = 0, 0
        row["input_tokens"] = 0
        row["output_tokens"] = 0
    tt = row.get("total_tokens")
    if tt is None:
        row["total_tokens"] = tin + tout
    else:
        try:
            row["total_tokens"] = int(tt)
        except (TypeError, ValueError):
            row["total_tokens"] = tin + tout
    if not isinstance(row.get("metadata"), dict):
        row["metadata"] = {}
    # messages: None means no structured messages; empty list treated same as None at insert.
    if "messages" not in row:
        row["messages"] = None
    promote_metadata_span_type_to_row(row)


def extract_miniobserve_correlation(request: Request) -> dict:
    prefix = "x-miniobserve-"
    out: dict = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk.startswith(prefix):
            suffix = lk[len(prefix):].strip().replace("-", "_")
            if suffix:
                out[suffix] = v
    return out


def _openai_assistant_response_text(msg: Any) -> str:
    if msg is None:
        return ""
    if isinstance(msg, dict):
        raw = msg.get("content")
        if isinstance(raw, str) and raw.strip():
            return raw[:4000]
        if isinstance(raw, list) and raw:
            try:
                return json.dumps(raw)[:4000]
            except Exception:
                return str(raw)[:4000]
        tc = msg.get("tool_calls")
        if tc:
            try:
                return json.dumps(tc)[:4000]
            except Exception:
                return str(tc)[:4000]
        return ""
    raw = getattr(msg, "content", None)
    if isinstance(raw, str) and raw.strip():
        return raw[:4000]
    tc = getattr(msg, "tool_calls", None)
    if tc:
        try:
            serialized = []
            for t in tc:
                if hasattr(t, "model_dump"):
                    serialized.append(t.model_dump())
                elif isinstance(t, dict):
                    serialized.append(t)
                else:
                    serialized.append(str(t))
            return json.dumps(serialized)[:4000]
        except Exception:
            return str(tc)[:4000]
    return ""


def normalize_raw_dump(provider: str, response: dict, request: Optional[dict] = None, latency_ms: float = 0) -> dict:
    raw_messages = None
    prompt = ""
    if request and isinstance(request.get("messages"), list):
        raw_messages = [
            {"role": m.get("role", "user"), "content": m.get("content") or ""}
            for m in request["messages"]
            if isinstance(m, dict)
        ]
        prompt = " | ".join(m["content"] for m in raw_messages)
    prompt = (prompt or "")[:4000]
    model = response.get("model") or "unknown"
    response_text = ""
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    if provider == "openai":
        usage = response.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        ptd = usage.get("prompt_tokens_details") or {}
        if isinstance(ptd, dict):
            cached_input_tokens = int(ptd.get("cached_tokens") or 0)
        cached_input_tokens = max(0, min(cached_input_tokens, input_tokens))
        choices = response.get("choices") or []
        if choices and isinstance(choices[0], dict):
            response_text = _openai_assistant_response_text(choices[0].get("message"))
    elif provider == "anthropic":
        usage = response.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        content = response.get("content") or []
        if isinstance(content, list):
            response_text = " ".join(
                (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) for b in content
            )[:4000]
        else:
            response_text = str(content)[:4000]
    else:
        response_text = str(response)[:4000]
    return {
        "app_name": "default",
        "model": model,
        "provider": provider,
        "prompt": prompt,
        "messages": raw_messages,
        "response": response_text,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "latency_ms": latency_ms,
        "cost_usd": 0.0,
        "error": None,
        "run_id": None,
        "span_name": None,
        "parent_span_id": None,
        "span_type": None,
        "metadata": {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _json_object_if_string(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _messages_from_request_object(request: Any) -> Optional[list]:
    """OpenAI-style ``request.messages`` from client bodies (e.g. Python ``Tracer``)."""
    if not isinstance(request, dict):
        return None
    raw = request.get("messages")
    if not isinstance(raw, list) or not raw:
        return None
    out = [
        {"role": m.get("role", "user"), "content": m.get("content") or ""}
        for m in raw
        if isinstance(m, dict)
    ]
    return out or None


def _is_tracer_llm_step_summary_prompt(prompt: str) -> bool:
    """``Tracer._span_to_log_body`` stores step + fingerprint JSON in ``prompt`` when chat is under ``request``."""
    o = _json_object_if_string(prompt)
    if not isinstance(o, dict):
        return False
    return "step" in o and "fingerprint" in o and "had_tool_call" in o


def _promote_request_messages_and_normalize_tracer_prompt(row: dict, body: dict) -> None:
    """
    Copy ``request.messages`` into ``messages`` when the client did not send top-level ``messages``.

    When the stored ``prompt`` is the Tracer step-summary JSON and we have structured messages,
    replace ``prompt`` with a joined plain-text preview so tables and cache-boundary UIs match chat.
    """
    msgs = row.get("messages")
    if not isinstance(msgs, list) or len(msgs) == 0:
        req = body.get("request")
        if isinstance(req, str) and req.strip():
            try:
                req = json.loads(req)
            except json.JSONDecodeError:
                req = None
        promoted = _messages_from_request_object(req) if isinstance(req, dict) else None
        if promoted is not None:
            row["messages"] = promoted
            msgs = promoted

    if isinstance(msgs, list) and len(msgs) > 0:
        p = row.get("prompt") or ""
        if isinstance(p, str) and _is_tracer_llm_step_summary_prompt(p):
            joined = " | ".join(str(m.get("content") or "") for m in msgs if isinstance(m, dict))
            if joined.strip():
                row["prompt"] = joined[:4000]


def openai_cached_tokens_from_completion_blob(blob: Any) -> Optional[int]:
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except json.JSONDecodeError:
            return None
    if not isinstance(blob, dict):
        return None
    usage = blob.get("usage")
    if not isinstance(usage, dict):
        return None
    ptd = usage.get("prompt_tokens_details")
    if not isinstance(ptd, dict) or "cached_tokens" not in ptd:
        return None
    try:
        return max(0, int(ptd.get("cached_tokens") or 0))
    except (TypeError, ValueError):
        return None


def _overlay_body_fields_on_row(row: dict, body: dict) -> None:
    if body.get("model") is not None:
        row["model"] = str(body["model"]).strip() or "unknown"
    if body.get("provider") is not None:
        row["provider"] = str(body["provider"]).strip() or "unknown"
    if body.get("prompt") is not None:
        row["prompt"] = (str(body["prompt"]) or "")[:4000]
    # Explicit messages array in body overrides what was extracted from request.messages.
    if isinstance(body.get("messages"), list):
        row["messages"] = [
            {"role": m.get("role", "user"), "content": m.get("content") or ""}
            for m in body["messages"]
            if isinstance(m, dict)
        ]
    for key, cast in (("input_tokens", int), ("output_tokens", int), ("total_tokens", int), ("latency_ms", float), ("cost_usd", float)):
        if body.get(key) is not None:
            try:
                row[key] = cast(body[key])
            except (TypeError, ValueError):
                pass
    if body.get("error") is not None:
        row["error"] = str(body["error"]) if body["error"] else None


def _structured_row_from_body(body: dict) -> dict:
    entry = LogEntry.model_validate(body)
    row = entry.model_dump()
    row["timestamp"] = row["timestamp"] or datetime.now(timezone.utc).isoformat()
    cached_from_usage = openai_cached_tokens_from_completion_blob(entry.response)
    if cached_from_usage is not None:
        try:
            inp = int(row.get("input_tokens") or 0)
            row["cached_input_tokens"] = max(0, min(cached_from_usage, inp))
        except (TypeError, ValueError):
            pass
    elif body.get("cached_input_tokens") is not None:
        try:
            inp = int(row.get("input_tokens") or 0)
            row["cached_input_tokens"] = max(0, min(int(body["cached_input_tokens"]), inp))
        except (TypeError, ValueError):
            pass
    row["prompt"] = (row["prompt"] or "")[:4000]
    row["response"] = (row["response"] or "")[:4000]
    return row


def row_from_log_request(body: dict, request: Request, app_name: str) -> tuple[dict, Optional[str], Optional[str]]:
    """
    Parse JSON body into a DB row dict.

    Returns (row, client_span_id, parent_client_span_id).
    client_span_id / parent_client_span_id are popped from body before validation.
    """
    client_span_id, parent_client_span_id = pop_client_span_correlation(body)
    try:
        if isinstance(body, dict) and body.get("response") is not None:
            resp_dict = _json_object_if_string(body["response"])
            if resp_dict is not None:
                req_dict = _json_object_if_string(body.get("request"))
                row = normalize_raw_dump(
                    provider=(body.get("provider") or "unknown"),
                    response=resp_dict,
                    request=req_dict,
                    latency_ms=float(body.get("latency_ms") or 0),
                )
                _overlay_body_fields_on_row(row, body)
                if isinstance(body.get("metadata"), dict):
                    row["metadata"] = body.get("metadata")
                for key in ("run_id", "span_name", "span_type"):
                    if isinstance(body.get(key), str):
                        row[key] = body.get(key)
                if body.get("parent_span_id") is not None:
                    try:
                        row["parent_span_id"] = int(body["parent_span_id"])
                    except (TypeError, ValueError):
                        pass
                if openai_cached_tokens_from_completion_blob(resp_dict) is None and body.get("cached_input_tokens") is not None:
                    try:
                        row["cached_input_tokens"] = int(body["cached_input_tokens"])
                    except (TypeError, ValueError):
                        pass
            else:
                row = _structured_row_from_body(body)
        else:
            row = _structured_row_from_body(body)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e

    row["app_name"] = app_name
    corr = extract_miniobserve_correlation(request)
    if corr:
        existing = row.get("metadata") or {}
        if not isinstance(existing, dict):
            existing = {}
        row["metadata"] = {**corr, **existing}
    if row.get("parent_span_id") is None:
        row["parent_span_id"] = None
    try:
        inp = int(row.get("input_tokens") or 0)
        c = int(row.get("cached_input_tokens") or 0)
        # Promote metadata.cache_read_tokens when cached_input_tokens was not sent explicitly.
        if c == 0:
            md = row.get("metadata") or {}
            if isinstance(md, dict):
                c = int(md.get("cache_read_tokens") or md.get("cache_read") or 0)
        row["cached_input_tokens"] = max(0, min(c, inp))
    except (TypeError, ValueError):
        row["cached_input_tokens"] = 0
    _promote_request_messages_and_normalize_tracer_prompt(row, body)
    ensure_log_row_for_db(row)
    sanitize_model_provider(row)
    attach_client_span_metadata(row, client_span_id)
    _merge_span_timestamps_into_metadata(row, body)
    return row, client_span_id, parent_client_span_id


def updates_from_patch_body(body: dict) -> dict:
    out: dict = {}
    str_keys = ("model", "provider", "prompt", "response", "run_id", "span_name", "timestamp", "span_type")
    for k in str_keys:
        if k not in body:
            continue
        v = body[k]
        if v is None:
            out[k] = None
        else:
            s = str(v).strip()
            out[k] = s if (k not in ("model", "provider") or s) else "unknown"
            if k in ("prompt", "response"):
                out[k] = out[k][:4000]
    if "error" in body:
        e = body["error"]
        out["error"] = None if e is None or e == "" else str(e)
    if "metadata" in body and isinstance(body["metadata"], dict):
        out["metadata"] = dict(body["metadata"])
    for k in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "parent_span_id"):
        if k in body and body[k] is not None:
            try:
                out[k] = int(body[k])
            except (TypeError, ValueError):
                pass
    for k in ("latency_ms", "cost_usd"):
        if k in body and body[k] is not None:
            try:
                out[k] = float(body[k])
            except (TypeError, ValueError):
                pass
    return out
