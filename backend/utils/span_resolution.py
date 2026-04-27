"""Resolve parent_client_span_id to DB parent_span_id (batch map or prior rows)."""
from __future__ import annotations

from typing import Dict, Optional

from state import db


def resolve_parent_client_span(
    row: dict,
    app_name: str,
    parent_client_span_id: Optional[str],
    batch_id_map: Optional[Dict[str, int]],
) -> None:
    """
    If row has no numeric parent_span_id, map parent_client_span_id to an integer parent.

    batch_id_map: client_span_id -> server row id for earlier entries in the same batch.
    Parents must appear before children in the batch array.
    """
    if row.get("parent_span_id") is not None:
        return
    if not parent_client_span_id:
        return
    run_id = (row.get("run_id") or "").strip()
    if not run_id:
        return
    pid: Optional[int] = None
    if batch_id_map:
        v = batch_id_map.get(parent_client_span_id)
        if v is not None:
            pid = int(v)
    if pid is None:
        found = db.lookup_log_id_by_client_span(app_name, run_id, parent_client_span_id)
        if found is not None:
            pid = int(found)
    if pid is not None:
        row["parent_span_id"] = pid
