"""Persist estimated USD cost on ingest when the client omits it."""
from __future__ import annotations

from typing import Any

from ingest.pricing import estimate_cost_usd


def fill_missing_cost_usd(row: dict) -> None:
    """Set row['cost_usd'] from model pricing when missing or zero."""
    try:
        if float(row.get("cost_usd") or 0) != 0:
            return
    except (TypeError, ValueError):
        pass
    est = estimate_cost_usd(row)
    if est > 0:
        row["cost_usd"] = round(float(est), 8)


def fill_missing_cost_usd_patch(merged_row: dict, updates: dict) -> None:
    """If PATCH merged row still has zero cost, add cost_usd to updates dict."""
    try:
        if float(merged_row.get("cost_usd") or 0) != 0:
            return
    except (TypeError, ValueError):
        return
    est = estimate_cost_usd(merged_row)
    if est > 0:
        updates["cost_usd"] = round(float(est), 8)


def coerce_row_for_pricing(d: dict[str, Any]) -> dict[str, Any]:
    """Normalize DB row shapes for estimate_cost_usd (ints, metadata dict)."""
    import json

    out = dict(d)
    md = out.get("metadata")
    if isinstance(md, str):
        try:
            out["metadata"] = json.loads(md or "{}")
        except json.JSONDecodeError:
            out["metadata"] = {}
    for k in ("input_tokens", "output_tokens", "cached_input_tokens"):
        try:
            out[k] = int(out.get(k) or 0)
        except (TypeError, ValueError):
            out[k] = 0
    return out
