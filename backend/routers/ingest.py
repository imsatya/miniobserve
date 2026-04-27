from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

import cognitive.run_compute as run_cognitive
from deps import get_app
from ingest.cost import coerce_row_for_pricing, fill_missing_cost_usd, fill_missing_cost_usd_patch
from ingest.trace import (
    ingest_db_exception_detail,
    merge_patch_span_timestamps_from_body,
    promote_metadata_span_type_for_patch,
    row_from_log_request,
    updates_from_patch_body,
)
from utils.run_utils import effective_run_key
from utils.span_resolution import resolve_parent_client_span
from state import db

router = APIRouter()


def _ingest_single_row(
    body: dict,
    request: Request,
    app_name: str,
    batch_id_map: dict | None,
) -> tuple[int, str | None, str]:
    row, client_span_id, parent_client_span_id = row_from_log_request(body, request, app_name)
    resolve_parent_client_span(row, app_name, parent_client_span_id, batch_id_map)
    fill_missing_cost_usd(row)
    row_id = db.insert_log(row)
    run_key = effective_run_key({**row, "id": row_id})
    return row_id, client_span_id, run_key


@router.post("/api/log")
async def log_entry(request: Request, app_name: str = Depends(get_app)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    try:
        row_id, _, _ = _ingest_single_row(body, request, app_name, None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=ingest_db_exception_detail(exc)) from exc

    print(f"[ingest] app={app_name} id={row_id}", flush=True)
    try:
        run_cognitive.recompute_after_ingest(app_name, row_id)
    except Exception as exc:
        print(f"[cognitive] recompute failed: {exc}", flush=True)
    return {"ok": True, "id": row_id}


@router.post("/api/logs")
async def log_entries_batch(request: Request, app_name: str = Depends(get_app)):
    """
    Atomically ingest ordered spans for one trace (typical use: flush a run).

    Each element uses the same shape as POST /api/log, plus optional:
      client_span_id, parent_client_span_id (strings; parent must refer to an
      earlier entry in the same batch or an already-stored row for the same run_id).

    Response: { ok, results: [{index, id, client_span_id}], id_map: {client_span_id: id} }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    entries = body.get("logs")
    if not isinstance(entries, list) or len(entries) == 0:
        raise HTTPException(status_code=400, detail="logs must be a non-empty array")

    id_map: dict[str, int] = {}
    results: list[dict] = []
    affected_runs: set[str] = set()

    for i, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"logs[{i}] must be an object")
        entry = dict(raw)
        try:
            row_id, client_span_id, rk = _ingest_single_row(entry, request, app_name, id_map)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=ingest_db_exception_detail(exc)) from exc
        if client_span_id:
            id_map[client_span_id] = row_id
        results.append({"index": i, "id": row_id, "client_span_id": client_span_id})
        affected_runs.add(rk)

    for rk in affected_runs:
        try:
            run_cognitive.recompute_run(app_name, rk)
        except Exception as exc:
            print(f"[cognitive] batch recompute failed: {exc}", flush=True)

    print(f"[ingest] batch app={app_name} n={len(entries)}", flush=True)
    return {"ok": True, "results": results, "id_map": id_map}


@router.patch("/api/log")
async def patch_log_entry(request: Request, app_name: str = Depends(get_app)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(body, dict) or body.get("id") is None:
        raise HTTPException(status_code=400, detail="id required")
    try:
        log_id = int(body["id"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid id")

    existing = db.fetch_log(log_id)
    if not existing:
        raise HTTPException(status_code=404, detail="not found")
    if (existing.get("app_name") or "") != app_name:
        raise HTTPException(status_code=404, detail="not found")

    updates = updates_from_patch_body({k: v for k, v in body.items() if k != "id"})
    merge_patch_span_timestamps_from_body(body, existing, updates)
    promote_metadata_span_type_for_patch(existing, updates)
    if not updates:
        raise HTTPException(status_code=400, detail="no updatable fields")
    try:
        inp = int(updates.get("input_tokens", -1))
        if inp >= 0 and "cached_input_tokens" in updates:
            c = int(updates["cached_input_tokens"] or 0)
            updates["cached_input_tokens"] = max(0, min(c, inp))
    except (TypeError, ValueError):
        pass

    merged = {**existing, **updates}
    merged = coerce_row_for_pricing(merged)
    fill_missing_cost_usd_patch(merged, updates)

    ok = db.update_log_row(app_name, log_id, updates)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    try:
        run_cognitive.recompute_after_ingest(app_name, log_id)
    except Exception as exc:
        print(f"[cognitive] recompute failed: {exc}", flush=True)
    return {"ok": True}
