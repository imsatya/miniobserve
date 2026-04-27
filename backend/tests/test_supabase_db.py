"""Supabase DB helpers: URL normalization (unit) and insert/patch round-trip (integration)."""

import os
from datetime import datetime, timezone

import httpx
import pytest
from postgrest.exceptions import APIError

import db.supabase as db_supabase
from db.tables import TABLE_LLM_LOGS


def _transient_api_error(exc: APIError) -> bool:
    """Cloudflare 521 / PostgREST 5xx — skip test instead of failing CI."""
    code = exc.code
    if code is None:
        return False
    try:
        c = int(code)
    except (TypeError, ValueError):
        return False
    return c in (502, 503, 521)


def _mo_tables_not_deployed(exc: APIError) -> bool:
    """PostgREST PGRST205 — project still on legacy table names; run supabase_migration.sql."""
    return str(getattr(exc, "code", "") or "") == "PGRST205"


def test_normalize_supabase_url_adds_scheme_and_preserves_host():
    assert db_supabase._normalize_supabase_url("abcxyz.supabase.co").startswith("https://")
    assert "abcxyz.supabase.co" in db_supabase._normalize_supabase_url("abcxyz.supabase.co")


def test_normalize_supabase_url_rejects_empty():
    with pytest.raises(RuntimeError, match="empty"):
        db_supabase._normalize_supabase_url("")


def _supabase_configured() -> bool:
    url = (
        os.environ.get("SUPABASE_URL", "").strip()
        or os.environ.get("PUBLIC_SUPABASE_URL", "").strip()
    )
    key = (
        os.environ.get("SUPABASE_KEY", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY", "").strip()
        or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )
    return bool(url and key)


@pytest.mark.skipif(not _supabase_configured(), reason="Supabase URL/key not in environment")
def test_supabase_insert_returns_id_and_update_log_row_works():
    """
    Integration: insert one row (postgrest insert().execute()), patch it, delete it.
    Requires mo_llm_logs + span_type column (see supabase_migration.sql).
    """
    db_supabase._client = None

    ts = datetime.now(timezone.utc).isoformat()
    app = "__miniobserve_pytest__"
    row = {
        "app_name": app,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "prompt": "",
        "response": "",
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "cost_usd": 0.0,
        "error": None,
        "run_id": "pytest-trace-roundtrip",
        "span_name": "pytest_span",
        "parent_span_id": None,
        "span_type": "llm",
        "metadata": {"pytest": True},
        "timestamp": ts,
    }

    rid = None
    try:
        try:
            rid = db_supabase.insert_log(row)
        except httpx.ConnectError as e:
            pytest.skip(f"Supabase host unreachable (DNS/network): {e}")
        except APIError as e:
            if _mo_tables_not_deployed(e):
                pytest.skip(
                    "Supabase has no mo_llm_logs (PGRST205); run backend/supabase_migration.sql on the project."
                )
            if _transient_api_error(e):
                pytest.skip(f"Supabase edge/API temporarily unavailable (HTTP {e.code}): {e.message or e}")
            raise

        assert isinstance(rid, int) and rid > 0

        ok = db_supabase.update_log_row(
            app,
            rid,
            {"latency_ms": 42.5, "input_tokens": 3, "output_tokens": 1},
        )
        assert ok is True

        sb = db_supabase._get_client()
        check = (
            sb.table(TABLE_LLM_LOGS)
            .select("id,latency_ms,input_tokens,output_tokens,span_type")
            .eq("id", rid)
            .single()
            .execute()
        )
        rec = check.data
        assert rec["latency_ms"] == 42.5
        assert rec["input_tokens"] == 3
        assert rec["output_tokens"] == 1
        assert rec.get("span_type") == "llm"
    finally:
        if rid is not None:
            try:
                db_supabase._get_client().table(TABLE_LLM_LOGS).delete().eq("id", rid).execute()
            except httpx.ConnectError:
                pass
