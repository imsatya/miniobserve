"""
One-shot HTTP check: post a single synthetic span so you can confirm the dashboard shows it.

Used by ``miniobserve hello`` and ``send_integration_hello``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import httpx

from .env_url import resolve_miniobserve_http_base_url
from .http_transport import request_json

# Must match backend ``LOCAL_DEFAULT_API_KEY`` and dashboard ``LOCAL_DEFAULT_API_KEY`` in api.js.
LOCAL_DEFAULT_API_KEY = "sk-local-default-key"


def _resolve_base_url() -> Optional[str]:
    """Match ``Tracer`` / ``MiniObserve`` URL resolution; ``None`` means no HTTP (stdout/off)."""
    return resolve_miniobserve_http_base_url()


def _auth_headers(*, base_url: str) -> dict[str, str]:
    key = (os.getenv("MINIOBSERVE_API_KEY") or "").strip()
    if not key:
        u = (base_url or "").lower()
        if "localhost" in u or "127.0.0.1" in u:
            key = LOCAL_DEFAULT_API_KEY
    h: dict[str, str] = {}
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def send_integration_hello(
    *,
    base_url: Optional[str] = None,
    timeout_s: float = 15.0,
) -> Tuple[bool, str, Optional[str], Optional[int]]:
    """
    POST ``/api/log`` with a recognizable hello-world row.

    Returns ``(ok, message_for_user, run_id_or_none, log_id_or_none)``.
    """
    url = (base_url or _resolve_base_url() or "").rstrip("/")
    if not url:
        return (
            False,
            "MINIOBSERVE_URL is stdout/off — enable HTTP (unset MINIOBSERVE_URL or set "
            "MINIOBSERVE_URL=http://127.0.0.1:7823) to verify against a real backend.",
            None,
            None,
        )

    run_id = uuid.uuid4().hex[:16]
    body = {
        "model": "integration-check",
        "provider": "miniobserve-cli",
        "prompt": "Hello — first MiniObserve check after SDK integration.",
        "response": "If you see this row in the dashboard, your client reached the backend successfully.",
        "input_tokens": 1,
        "output_tokens": 12,
        "latency_ms": 1.0,
        "cost_usd": 0.0,
        "run_id": run_id,
        "span_name": "hello_first_integration",
        "span_type": "llm",
        "metadata": {
            "hello_world": True,
            "source": "miniobserve hello",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **_auth_headers(base_url=url),
    }

    with httpx.Client(timeout=timeout_s) as client:
        hr, _ = request_json(client, "GET", f"{url}/api/health", headers=headers)
        if hr is None or hr.status_code != 200:
            return (
                False,
                f"Backend health check failed at {url}/api/health "
                f"(start the server: cd backend && uvicorn main:app --port 7823).",
                run_id,
                None,
            )

        r, data = request_json(
            client,
            "POST",
            f"{url}/api/log",
            json_body=body,
            headers=headers,
        )

    if r is None:
        return (False, "Network error posting /api/log (timeout or connection refused).", run_id, None)
    if r.status_code == 401:
        return (
            False,
            "401 Unauthorized — set MINIOBSERVE_API_KEY to a key allowed for this server (see AGENTS.md).",
            run_id,
            None,
        )
    if r.status_code >= 400:
        detail = ""
        try:
            detail = json.dumps(r.json())
        except Exception:
            detail = (r.text or "")[:500]
        return (False, f"Ingest failed HTTP {r.status_code}: {detail}", run_id, None)

    log_id = None
    if isinstance(data, dict):
        log_id = data.get("id")
        if log_id is not None:
            try:
                log_id = int(log_id)
            except (TypeError, ValueError):
                log_id = None

    dash = url.replace("0.0.0.0", "127.0.0.1")
    msg = (
        f"Posted hello row (run_id={run_id}, id={log_id}).\n"
        f"Open the dashboard: {dash}/\n"
        f"  → Runs: look for run keyed by this run_id, or search prompts for \"Hello — first\".\n"
        f"  → Backend log should show an [ingest] line for this request."
    )
    return (True, msg, run_id, log_id)
