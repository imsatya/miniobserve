import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import ingest.pricing as pricing
from deps import get_app
from state import db

router = APIRouter()


@router.get("/api/logs")
def get_logs(
    app_name: str = Depends(get_app),
    limit: int = Query(50, le=500),
    offset: int = 0,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    has_error: Optional[bool] = None,
    search: Optional[str] = None,
):
    total, logs = db.query_logs(
        limit=limit,
        offset=offset,
        model=model,
        provider=provider,
        app_name=app_name,
        has_error=has_error,
        search=search,
    )
    return {"total": total, "logs": pricing.enrich_logs(logs)}


@router.get("/api/logs/{log_id}")
def get_log(log_id: int, app_name: str = Depends(get_app)):
    row = db.fetch_log(log_id)
    if not row or row.get("app_name") != app_name:
        raise HTTPException(status_code=404, detail="not found")
    return pricing.enrich_log_row(row)


@router.get("/api/stats")
def get_stats(app_name: str = Depends(get_app)):
    rows = db.fetch_cost_estimate_rows(app_name)
    return pricing.aggregate_stats(rows)


@router.get("/api/replay/{log_id}")
def get_replay(log_id: int, app_name: str = Depends(get_app)):
    row = db.fetch_log(log_id)
    if not row or row.get("app_name") != app_name:
        raise HTTPException(status_code=404, detail="not found")
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {
        "model": row["model"],
        "provider": row["provider"],
        "prompt": row["prompt"],
        "metadata": metadata,
    }


@router.delete("/api/logs")
def clear_logs(app_name: str = Depends(get_app)):
    """Delete all logs and run summaries for the app. API access log is in-memory only and resets on restart."""
    db.delete_logs(app_name)
    return {"ok": True}
