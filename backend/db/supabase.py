"""Supabase database backend.

Environment (first match wins for URL / key):
    SUPABASE_URL or PUBLIC_SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY, then SUPABASE_KEY, then publishable/anon fallbacks.

Use the service_role secret for the backend so PostgREST bypasses RLS. If both
SUPABASE_KEY (anon) and SUPABASE_SERVICE_ROLE_KEY are set, the service role wins.
Anon/publishable keys only work if RLS is off on mo_llm_logs (see supabase_migration.sql)
or you add explicit policies.

Run backend/supabase_migration.sql once in the Supabase SQL editor to create tables (mo_llm_logs, etc.).
"""
import os
import json
from typing import Optional
from urllib.parse import urlparse

from supabase import create_client, Client

from db.tables import TABLE_API_KEY_CREDENTIALS, TABLE_LLM_LOGS, TABLE_RUN_SUMMARIES
from utils.run_utils import effective_run_key

# Must match log_ingest.MINIOBSERVE_CLIENT_SPAN_META_KEY
_CLIENT_SPAN_META_KEY = "miniobserve_client_span_id"

_client: Client = None


def _normalize_supabase_url(raw: str) -> str:
    """
    Ensure a valid https URL with a hostname. Accepts 'xxxx.supabase.co' without scheme.
    """
    u = (raw or "").strip()
    if not u:
        raise RuntimeError(
            "Invalid SUPABASE_URL / PUBLIC_SUPABASE_URL: empty or whitespace only"
        )
    if "://" not in u:
        u = "https://" + u
    parsed = urlparse(u)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            "Invalid SUPABASE_URL / PUBLIC_SUPABASE_URL: missing hostname. "
            f"Use https://<project-ref>.supabase.co — got {raw!r}"
        )
    return u


def _supabase_url() -> str:
    return (
        os.environ.get("SUPABASE_URL", "").strip()
        or os.environ.get("PUBLIC_SUPABASE_URL", "").strip()
    )


def _supabase_key() -> str:
    # Prefer explicit service_role so a leftover anon SUPABASE_KEY does not break ingest (RLS 42501).
    return (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SUPABASE_KEY", "").strip()
        or os.environ.get("PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY", "").strip()
        or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )


def _get_client() -> Client:
    global _client
    if _client is None:
        raw_url = _supabase_url()
        key = _supabase_key()
        if not raw_url or not key:
            raise RuntimeError(
                "Supabase is configured but no URL/key pair was found. "
                "Set SUPABASE_URL (or PUBLIC_SUPABASE_URL) and "
                "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY (service_role recommended), "
                "or a publishable/anon key if RLS is disabled on mo_llm_logs (see supabase_migration.sql)."
            )
        url = _normalize_supabase_url(raw_url)
        _client = create_client(url, key)
    return _client


def init():
    # Table must be created manually via SQL above.
    # Verify connection is working.
    _get_client()


def insert_log(row: dict) -> int:
    """Insert one row; returns new row id."""
    sb = _get_client()
    md = row.get("metadata")
    if not isinstance(md, dict):
        md = {}
    payload = {
        "app_name": row["app_name"],
        "model": row["model"],
        "provider": row["provider"],
        "prompt": row["prompt"],
        "response": row["response"],
        "input_tokens": row["input_tokens"],
        "cached_input_tokens": int(row.get("cached_input_tokens") or 0),
        "output_tokens": row["output_tokens"],
        "total_tokens": row["total_tokens"],
        "latency_ms": row["latency_ms"],
        "cost_usd": row["cost_usd"],
        "error": row["error"],
        "run_id": row["run_id"],
        "span_name": row["span_name"],
        "metadata": md,
        "timestamp": row["timestamp"],
    }
    if row.get("parent_span_id") is not None:
        payload["parent_span_id"] = row["parent_span_id"]
    if row.get("span_type") is not None:
        payload["span_type"] = row["span_type"]
    if row.get("cognitive_mode") is not None:
        payload["cognitive_mode"] = row["cognitive_mode"]
    if row.get("cognitive_stuck") is not None:
        payload["cognitive_stuck"] = bool(row["cognitive_stuck"])
    if row.get("cognitive_waiting") is not None:
        payload["cognitive_waiting"] = bool(row["cognitive_waiting"])
    msgs = row.get("messages")
    if isinstance(msgs, list) and msgs:
        payload["messages"] = msgs
    # postgrest v2: .insert() returns SyncQueryRequestBuilder (no .chained .select()); use default returning=representation.
    result = sb.table(TABLE_LLM_LOGS).insert(payload).execute()
    rows = result.data or []
    if rows:
        return int(rows[0]["id"])
    raise RuntimeError(
        "Supabase insert did not return id; run supabase_migration.sql (span_type, etc.) and ensure "
        "the table is writable with your API key."
    )


def update_log_row(app_name: str, log_id: int, updates: dict) -> bool:
    """Merge updates into an existing row (same app only)."""
    if not updates:
        return False
    allowed = {
        "model", "provider", "prompt", "response", "input_tokens", "cached_input_tokens",
        "output_tokens", "total_tokens", "latency_ms", "cost_usd", "error",
        "run_id", "span_name", "parent_span_id", "span_type", "cognitive_mode",
        "cognitive_stuck", "cognitive_waiting", "metadata", "timestamp",
    }
    patch = {k: v for k, v in updates.items() if k in allowed}
    if not patch:
        return False
    sb = _get_client()
    if "metadata" in patch and isinstance(patch["metadata"], str):
        try:
            patch["metadata"] = json.loads(patch["metadata"])
        except Exception:
            patch["metadata"] = {}
    res = sb.table(TABLE_LLM_LOGS).update(patch).eq("app_name", app_name).eq("id", log_id).execute()
    return bool(res.data)


def query_logs(*, limit, offset, model, provider, app_name, has_error, search):
    sb = _get_client()

    # Count query via rpc for complex filters
    # Build filter for main query
    q = sb.table(TABLE_LLM_LOGS).select("*", count="exact")

    if model:
        q = q.eq("model", model)
    if provider:
        q = q.eq("provider", provider)
    if app_name:
        q = q.eq("app_name", app_name)
    if has_error is True:
        q = q.not_.is_("error", "null")
    elif has_error is False:
        q = q.is_("error", "null")
    if search:
        q = q.or_(f"prompt.ilike.%{search}%,response.ilike.%{search}%,model.ilike.%{search}%")

    result = q.order("timestamp", desc=True).range(offset, offset + limit - 1).execute()
    total = result.count or 0
    return total, result.data or []


def fetch_cost_estimate_rows(app_name: str):
    """All logs for pricing + stats aggregation (capped; raise cap if needed)."""
    sb = _get_client()
    q = sb.table(TABLE_LLM_LOGS).select(
        "model,provider,input_tokens,output_tokens,cached_input_tokens,cost_usd,latency_ms,error,total_tokens,timestamp"
    )
    if app_name:
        q = q.eq("app_name", app_name)
    result = q.limit(50000).execute()
    return result.data or []


def fetch_log(log_id: int):
    sb = _get_client()
    result = sb.table(TABLE_LLM_LOGS).select("*").eq("id", log_id).single().execute()
    return result.data


def lookup_log_id_by_client_span(app_name: str, run_id: str, client_span_id: str) -> Optional[int]:
    """Find server row id for a prior span in the same run (opaque client id in metadata)."""
    if not app_name or not run_id or not client_span_id:
        return None
    sb = _get_client()
    res = (
        sb.table(TABLE_LLM_LOGS)
        .select("id,metadata")
        .eq("app_name", app_name)
        .eq("run_id", run_id)
        .order("id")
        .execute()
    )
    for row in res.data or []:
        md = row.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except json.JSONDecodeError:
                md = {}
        if isinstance(md, dict) and md.get(_CLIENT_SPAN_META_KEY) == client_span_id:
            return int(row["id"])
    return None


def fetch_stats(app_name):
    sb = _get_client()

    # Use raw SQL via rpc for aggregations
    # Requires a Postgres function — see below for setup, or use the inline approach
    filters = f"app_name = '{app_name}'" if app_name else "TRUE"

    agg_result = sb.rpc("miniobserve_stats", {"filter_app": app_name or ""}).execute()

    # Fallback: compute from raw data if rpc not set up
    if not agg_result.data:
        return _fetch_stats_fallback(sb, app_name)

    agg = agg_result.data[0]
    models_result = sb.rpc("miniobserve_models", {"filter_app": app_name or ""}).execute()
    daily_result = sb.rpc("miniobserve_daily", {"filter_app": app_name or ""}).execute()

    return agg, models_result.data or [], daily_result.data or []


def _fetch_stats_fallback(sb, app_name):
    """Compute stats client-side if RPC functions aren't installed.
    Not efficient for large datasets — install the SQL functions instead.
    """
    q = sb.table(TABLE_LLM_LOGS).select(
        "total_tokens,cost_usd,latency_ms,error,input_tokens,output_tokens,model,provider,timestamp"
    )
    if app_name:
        q = q.eq("app_name", app_name)
    rows = q.execute().data or []

    total_calls = len(rows)
    total_tokens = sum(r.get("total_tokens") or 0 for r in rows)
    total_cost = sum(r.get("cost_usd") or 0 for r in rows)
    avg_latency = (sum(r.get("latency_ms") or 0 for r in rows) / total_calls) if total_calls else 0
    error_count = sum(1 for r in rows if r.get("error"))
    total_input = sum(r.get("input_tokens") or 0 for r in rows)
    total_output = sum(r.get("output_tokens") or 0 for r in rows)

    agg = {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "avg_latency_ms": avg_latency,
        "error_count": error_count,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
    }

    # Models breakdown
    model_map = {}
    for r in rows:
        key = (r["model"], r["provider"])
        if key not in model_map:
            model_map[key] = {"model": r["model"], "provider": r["provider"], "calls": 0, "tokens": 0, "cost": 0.0, "latencies": []}
        model_map[key]["calls"] += 1
        model_map[key]["tokens"] += r.get("total_tokens") or 0
        model_map[key]["cost"] += r.get("cost_usd") or 0
        model_map[key]["latencies"].append(r.get("latency_ms") or 0)
    models = []
    for m in sorted(model_map.values(), key=lambda x: -x["calls"])[:10]:
        lats = m.pop("latencies")
        m["avg_latency"] = sum(lats) / len(lats) if lats else 0
        models.append(m)

    # Daily breakdown (last 14 days)
    from collections import defaultdict
    day_map = defaultdict(lambda: {"calls": 0, "cost": 0.0, "tokens": 0})
    for r in rows:
        ts = r.get("timestamp")
        day = str(ts)[:10] if ts is not None else ""
        if day:
            day_map[day]["calls"] += 1
            day_map[day]["cost"] += r.get("cost_usd") or 0
            day_map[day]["tokens"] += r.get("total_tokens") or 0
    daily = [{"day": d, **v} for d, v in sorted(day_map.items())[-14:]]

    return agg, models, daily


def delete_logs(app_name):
    sb = _get_client()
    if app_name:
        try:
            sb.table(TABLE_RUN_SUMMARIES).delete().eq("app_name", app_name).execute()
        except Exception:
            pass
    q = sb.table(TABLE_LLM_LOGS)
    if app_name:
        q.delete().eq("app_name", app_name).execute()
    else:
        q.delete().neq("id", 0).execute()  # delete all


def distinct_app_names() -> list:
    """Distinct app_name values (bounded scan for PostgREST)."""
    sb = _get_client()
    seen = set()
    batch = 5000
    offset = 0
    while offset < 400000:
        res = (
            sb.table(TABLE_LLM_LOGS)
            .select("app_name")
            .order("id", desc=False)
            .range(offset, offset + batch - 1)
            .execute()
        )
        rows = res.data or []
        for row in rows:
            a = row.get("app_name")
            if a:
                seen.add(a)
        if len(rows) < batch:
            break
        offset += batch
    return sorted(seen)


def fetch_recent_logs(app_name: str, limit: int = 8000):
    sb = _get_client()
    q = sb.table(TABLE_LLM_LOGS).select("*").eq("app_name", app_name)
    result = q.order("timestamp", desc=True).limit(limit).execute()
    return result.data or []


def fetch_run_logs(app_name: str, run_key: str):
    """Steps for one run; uses run_id / metadata filters and merges."""
    import json as _json
    sb = _get_client()
    if run_key.startswith("orphan-"):
        try:
            oid = int(run_key.split("-", 1)[1])
        except ValueError:
            return []
        r = sb.table(TABLE_LLM_LOGS).select("*").eq("app_name", app_name).eq("id", oid).execute()
        rows = r.data or []
        return sorted(rows, key=lambda x: x.get("timestamp") or "")

    seen = {}
    r0 = sb.table(TABLE_LLM_LOGS).select("*").eq("app_name", app_name).eq("run_id", run_key).execute()
    for row in r0.data or []:
        seen[row["id"]] = row

    try:
        rj = sb.table(TABLE_LLM_LOGS).select("*").eq("app_name", app_name).contains(
            "metadata", {"run_id": run_key}
        ).execute()
        for row in rj.data or []:
            seen[row["id"]] = row
    except Exception:
        pass

    if not seen:
        # Fallback: scan recent rows (last 15k) and match in Python
        recent = fetch_recent_logs(app_name, limit=15000)
        for row in recent:
            if effective_run_key(row) == run_key:
                seen[row["id"]] = row

    return sorted(seen.values(), key=lambda x: x.get("timestamp") or "")


def batch_set_cognitive_modes(app_name: str, pairs: list) -> None:
    if not pairs:
        return
    sb = _get_client()
    for item in pairs:
        log_id, mode, stuck, waiting = item
        patch = {
            "cognitive_mode": mode,
            "cognitive_stuck": bool(stuck),
            "cognitive_waiting": bool(waiting),
        }
        sb.table(TABLE_LLM_LOGS).update(patch).eq("app_name", app_name).eq("id", int(log_id)).execute()


def upsert_run_summary(app_name: str, run_key: str, data: dict) -> None:
    sb = _get_client()
    row = {
        "app_name": app_name,
        "run_key": run_key,
        "mode_fractions": data.get("mode_fractions") or {},
        "fingerprint_segments": data.get("fingerprint_segments") or [],
        "stuck_alerts": data.get("stuck_alerts") or [],
        "call_trace_segments": data.get("call_trace_segments") or [],
    }
    sb.table(TABLE_RUN_SUMMARIES).upsert(row, on_conflict="app_name,run_key").execute()


def fetch_run_summaries_batch(app_name: str, run_keys: list) -> dict:
    import json as _json

    if not run_keys:
        return {}
    uniq = list(dict.fromkeys(run_keys))
    sb = _get_client()
    res = sb.table(TABLE_RUN_SUMMARIES).select("*").eq("app_name", app_name).in_("run_key", uniq).execute()
    out = {}
    for d in res.data or []:
        rk = d.get("run_key")
        if not rk:
            continue
        for k in ("mode_fractions", "fingerprint_segments", "stuck_alerts", "call_trace_segments"):
            v = d.get(k)
            if isinstance(v, str):
                try:
                    d[k] = _json.loads(v)
                except Exception:
                    d[k] = {} if k == "mode_fractions" else []
        out[rk] = d
    return out


def insert_api_key_credential(
    key_hash: str, app_name: str, *, label: Optional[str] = None, source: str = "admin"
) -> int:
    sb = _get_client()
    payload = {
        "key_hash": key_hash,
        "app_name": app_name,
        "label": label or None,
        "source": source,
    }
    result = sb.table(TABLE_API_KEY_CREDENTIALS).insert(payload).execute()
    rows = result.data or []
    if rows:
        return int(rows[0]["id"])
    return 0


def resolve_api_key_app_name(key_hash: str) -> Optional[str]:
    try:
        sb = _get_client()
        res = (
            sb.table(TABLE_API_KEY_CREDENTIALS)
            .select("app_name")
            .eq("key_hash", key_hash)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        return rows[0].get("app_name")
    except Exception:
        return None
