import json
from collections import defaultdict
from fastapi import APIRouter, Depends, Query

import ingest.pricing as pricing
import cognitive.run_compute as run_cognitive
import utils.run_utils as run_utils
from deps import get_app
from state import db

router = APIRouter()


@router.get("/api/runs")
def list_runs(
    app_name: str = Depends(get_app),
    scan_limit: int = Query(8000, le=50000, ge=100),
    runs_limit: int = Query(100, le=500, ge=1),
):
    rows = pricing.enrich_logs(db.fetch_recent_logs(app_name, limit=scan_limit))
    summaries = run_utils.aggregate_runs(rows)
    keys = [s["run_key"] for s in summaries]
    batch = db.fetch_run_summaries_batch(app_name, keys)
    rows_by_key: defaultdict[str, list] = defaultdict(list)
    for row in rows:
        rows_by_key[run_utils.effective_run_key(row)].append(row)
    merged = [
        run_cognitive.enrich_run_list_item(s, s["run_key"], batch.get(s["run_key"]))
        for s in summaries
    ]
    for m in merged:
        if not m.get("call_trace_segments"):
            m["call_trace_segments"] = run_utils.call_trace_segments_for_run_rows(
                rows_by_key.get(m["run_key"], [])
            )
    return {"runs": merged[:runs_limit]}


@router.get("/api/run-logs")
def get_run_logs_detail(
    run_key: str = Query(..., min_length=1),
    app_name: str = Depends(get_app),
):
    steps = pricing.enrich_logs(db.fetch_run_logs(app_name, run_key))
    steps = run_cognitive.enrich_steps_with_cognitive(steps)
    analysis = run_utils.analyze_run(steps)
    cache_breakdown = run_utils.cache_breakdown_for_run(steps)
    summ = db.fetch_run_summaries_batch(app_name, [run_key]).get(run_key) or {}
    cognitive = {
        "mode_fractions": summ.get("mode_fractions") if isinstance(summ.get("mode_fractions"), dict) else {},
        "stuck_alerts": summ.get("stuck_alerts") if isinstance(summ.get("stuck_alerts"), list) else [],
        "fingerprint_segments": summ.get("fingerprint_segments") if isinstance(summ.get("fingerprint_segments"), list) else [],
        "call_trace_segments": summ.get("call_trace_segments") if isinstance(summ.get("call_trace_segments"), list) else [],
    }
    if isinstance(cognitive["mode_fractions"], str):
        try:
            cognitive["mode_fractions"] = json.loads(cognitive["mode_fractions"])
        except Exception:
            cognitive["mode_fractions"] = {}
    return {
        "run_key": run_key,
        "steps": steps,
        "analysis": analysis,
        "cache_breakdown": cache_breakdown,
        "cognitive": cognitive,
    }


@router.get("/api/replay/run")
def get_replay_run(
    run_key: str = Query(..., min_length=1),
    app_name: str = Depends(get_app),
):
    steps = db.fetch_run_logs(app_name, run_key)
    out = []
    for s in steps:
        md = s.get("metadata") or {}
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except Exception:
                md = {}
        out.append(
            {
                "id": s.get("id"),
                "model": s.get("model"),
                "provider": s.get("provider"),
                "prompt": s.get("prompt"),
                "span_name": s.get("span_name"),
                "metadata": md,
                "timestamp": s.get("timestamp"),
            }
        )
    return {"run_key": run_key, "steps": out}
