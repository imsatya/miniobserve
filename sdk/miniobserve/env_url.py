"""Resolve MiniObserve HTTP base URL from environment (Tracer, Observer, CLI verify)."""
from __future__ import annotations

import os
from typing import Optional


def resolve_miniobserve_http_base_url() -> Optional[str]:
    """
    Base URL for the MiniObserve HTTP API (no trailing slash).

    Returns ``None`` when ingest is disabled (``MINIOBSERVE_URL`` is set and empty / stdout / off).

    Resolution:

    1. If ``MINIOBSERVE_URL`` appears in the environment, its value wins (strip; empty or
       ``stdout`` / ``off`` / ``0`` / ``false`` → ``None``).
    2. If ``MINIOBSERVE_URL`` is **not** set, use ``MINIOBSERVE_DASHBOARD_ORIGIN`` when set — the
       same origin you open in the browser (e.g. ``https://your-tunnel.example``) so the client posts
       to the same backend the dashboard uses.
    3. Otherwise ``http://localhost:7823``.
    """
    if "MINIOBSERVE_URL" in os.environ:
        v = (os.environ.get("MINIOBSERVE_URL") or "").strip()
        if not v or v.lower() in ("stdout", "off", "0", "false"):
            return None
        return v.rstrip("/")
    dash = (os.environ.get("MINIOBSERVE_DASHBOARD_ORIGIN") or "").strip()
    if dash and dash.lower() not in ("stdout", "off", "0", "false"):
        return dash.rstrip("/")
    return "http://localhost:7823"
