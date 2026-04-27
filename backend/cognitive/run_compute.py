"""Recompute cognitive modes and run summaries after ingest (server-side)."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from cognitive.modes import compute_cognitive_for_run, is_session_envelope_row
from utils.run_utils import effective_run_key

if os.environ.get("MINIOBSERVE_BACKEND", "sqlite").lower() == "supabase":
    import db.supabase as db
else:
    import db.sqlite as db


def recompute_after_ingest(app_name: str, log_id: int) -> None:
    """Call after POST /api/log or PATCH /api/log for a row in app_name."""
    try:
        row = db.fetch_log(log_id)
    except Exception:
        return
    if not row:
        return
    if (row.get("app_name") or "") != app_name:
        return
    run_key = effective_run_key(row)
    recompute_run(app_name, run_key)


def recompute_run(app_name: str, run_key: str) -> None:
    steps = db.fetch_run_logs(app_name, run_key)
    if not steps:
        return

    (
        phases_by_id,
        stuck_by_id,
        waiting_by_id,
        mode_fractions,
        fingerprint_segments,
        stuck_alerts,
        call_trace_segments,
    ) = compute_cognitive_for_run(steps)

    all_ids = {int(s["id"]) for s in steps if int(s.get("id") or 0)}
    pairs = [
        (
            lid,
            phases_by_id.get(lid, "unknown"),
            stuck_by_id.get(lid, False),
            waiting_by_id.get(lid, False),
        )
        for lid in sorted(all_ids)
    ]
    db.batch_set_cognitive_modes(app_name, pairs)

    db.upsert_run_summary(
        app_name,
        run_key,
        {
            "mode_fractions": mode_fractions,
            "fingerprint_segments": fingerprint_segments,
            "stuck_alerts": stuck_alerts,
            "call_trace_segments": call_trace_segments,
        },
    )


def backfill_cognitive_runs(
    *,
    app_name: Optional[str] = None,
    scan_limit: int = 100_000,
) -> Dict[str, Any]:
    """
    Recompute cognitive_mode + mo_run_summaries for existing rows (classifier + UI refresh).

    Scans recent mo_llm_logs per app, derives distinct run keys, calls recompute_run for each.
    Run after migrations or when improving infer_is_tool_span / compute_cognitive_for_run.
    """
    apps: List[str] = [app_name] if app_name else db.distinct_app_names()
    if not apps:
        return {"apps": 0, "runs": 0, "errors": []}

    errors: List[dict] = []
    run_count = 0
    for app in apps:
        rows = db.fetch_recent_logs(app, limit=scan_limit)
        keys = {effective_run_key(row) for row in rows}
        for rk in sorted(keys):
            try:
                recompute_run(app, rk)
                run_count += 1
            except Exception as e:
                errors.append({"app_name": app, "run_key": rk, "error": str(e)})
    return {"apps": len(apps), "runs": run_count, "errors": errors}


def enrich_steps_with_cognitive(steps: list) -> list:
    """Attach cognitive fields for API (steps already have DB columns)."""
    out = []
    for s in steps:
        x = dict(s)
        if is_session_envelope_row(s):
            x["cognitive_mode"] = None
            x["cognitive_stuck"] = False
            x["cognitive_waiting"] = False
        else:
            cm = (s.get("cognitive_mode") or "").strip() or None
            x["cognitive_mode"] = cm
            x["cognitive_stuck"] = bool(s.get("cognitive_stuck"))
            x["cognitive_waiting"] = bool(s.get("cognitive_waiting"))
        out.append(x)
    return out


def enrich_run_list_item(summary: dict, run_key: str, row: dict | None) -> dict:
    """Merge mo_run_summaries row into aggregate_runs item."""
    s = dict(summary)
    if not row:
        s.setdefault("mode_fractions", {})
        s.setdefault("fingerprint_segments", [])
        s.setdefault("call_trace_segments", [])
        return s
    mf = row.get("mode_fractions")
    if isinstance(mf, str):
        try:
            mf = json.loads(mf)
        except Exception:
            mf = {}
    s["mode_fractions"] = mf if isinstance(mf, dict) else {}
    fp = row.get("fingerprint_segments")
    if isinstance(fp, str):
        try:
            fp = json.loads(fp)
        except Exception:
            fp = []
    fp = fp if isinstance(fp, list) else []
    mf = s.get("mode_fractions") or {}
    if not fp and isinstance(mf, dict) and mf:
        order = ("routing", "planning", "acting", "observing", "dispatching", "unknown")
        fp = [{"mode": k, "fraction": float(mf[k])} for k in order if mf.get(k) is not None and float(mf[k]) > 0]
    s["fingerprint_segments"] = fp
    ct = row.get("call_trace_segments")
    if isinstance(ct, str):
        try:
            ct = json.loads(ct)
        except Exception:
            ct = []
    s["call_trace_segments"] = ct if isinstance(ct, list) else []
    return s
