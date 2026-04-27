"""Estimate display cost from model + token counts using data/model_pricing.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "model_pricing.json"
_pricing_cache: Optional[Dict[str, Any]] = None
_openai_by_len: List[dict] = []
_anthropic_by_len: List[dict] = []


def _load_pricing() -> Dict[str, Any]:
    global _pricing_cache, _openai_by_len, _anthropic_by_len
    if _pricing_cache is not None:
        return _pricing_cache
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    _pricing_cache = raw
    _openai_by_len = sorted(raw.get("openai") or [], key=lambda x: len(x.get("id") or ""), reverse=True)
    _anthropic_by_len = sorted(raw.get("anthropic") or [], key=lambda x: len(x.get("id") or ""), reverse=True)
    return raw


def normalize_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p == "openai":
        return "openai"
    if p == "anthropic":
        return "anthropic"
    return p


def effective_provider_for_pricing(row: Dict[str, Any]) -> str:
    """Map openrouter / unknown provider to openai or anthropic from model id when possible."""
    p = normalize_provider(str(row.get("provider") or ""))
    if p in ("openai", "anthropic"):
        return p
    m = _strip_router_prefix(str(row.get("model") or "")).lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "o4", "davinci", "babbage")):
        return "openai"
    return ""


def _strip_router_prefix(model: str) -> str:
    """openai/gpt-4o -> gpt-4o; anthropic/claude-3-5-sonnet-... -> last segment."""
    m = (model or "").strip()
    if "/" in m:
        return m.split("/")[-1].strip()
    return m


def _find_row(provider: str, model: str) -> Optional[dict]:
    _load_pricing()
    model_l = _strip_router_prefix(model).lower()
    if not model_l:
        return None
    entries = _openai_by_len if provider == "openai" else _anthropic_by_len if provider == "anthropic" else []
    for e in entries:
        mid = (e.get("id") or "").lower()
        if not mid:
            continue
        if model_l == mid or model_l.startswith(mid + "-") or model_l.startswith(mid + "@"):
            return e
    return None


def estimate_cost_usd(row: Dict[str, Any]) -> float:
    """
    Estimated USD from pricing table. Returns 0 if unknown model or no token usage.
    OpenAI: uncached prompt at input rate, cached prompt at cached_input rate, completion at output rate.
    Anthropic: uncached at base input, cached reads at cache_read rate, completion at output rate.
    """
    provider = effective_provider_for_pricing(row)
    if provider not in ("openai", "anthropic"):
        return 0.0
    pr = _find_row(provider, str(row.get("model") or ""))
    if not pr:
        return 0.0

    inp = int(row.get("input_tokens") or 0)
    out = int(row.get("output_tokens") or 0)
    cached = int(row.get("cached_input_tokens") or 0)
    if inp == 0 and out == 0:
        return 0.0
    cached = max(0, min(cached, inp))
    uncached = inp - cached

    if provider == "openai":
        rin = float(pr["input_usd_per_million"])
        rc = pr.get("cached_input_usd_per_million")
        rcache = float(rc) if rc is not None else rin
        rout = float(pr["output_usd_per_million"])
        return (uncached / 1_000_000.0) * rin + (cached / 1_000_000.0) * rcache + (out / 1_000_000.0) * rout

    rin = float(pr["input_usd_per_million"])
    rread = float(pr.get("cache_read_input_usd_per_million") or rin)
    rout = float(pr["output_usd_per_million"])
    return (uncached / 1_000_000.0) * rin + (cached / 1_000_000.0) * rread + (out / 1_000_000.0) * rout


def display_cost_usd(row: Dict[str, Any]) -> float:
    """Prefer table-based estimate when available; otherwise stored cost_usd."""
    est = estimate_cost_usd(row)
    if est > 0:
        return est
    return float(row.get("cost_usd") or 0)


def enrich_log_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out["cost_usd"] = round(display_cost_usd(out), 8)
    return out


def enrich_logs(rows: List[dict]) -> List[dict]:
    return [enrich_log_row(r) for r in rows]


def aggregate_stats(rows: List[dict]) -> dict:
    """
    Build /api/stats payload: totals, models breakdown, daily series — costs from pricing when possible.
    """
    if not rows:
        return {
            "total_calls": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "avg_latency_ms": 0.0,
            "error_count": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "error_rate_pct": 0.0,
            "models": [],
            "daily": [],
        }

    total_calls = len(rows)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in rows)
    total_cost = sum(display_cost_usd(r) for r in rows)
    total_lat = sum(float(r.get("latency_ms") or 0) for r in rows)
    error_count = sum(1 for r in rows if r.get("error"))
    total_in = sum(int(r.get("input_tokens") or 0) for r in rows)
    total_out = sum(int(r.get("output_tokens") or 0) for r in rows)

    from collections import defaultdict

    model_map: Dict[Tuple[str, str], dict] = {}
    for r in rows:
        key = (str(r.get("model") or ""), str(r.get("provider") or ""))
        if key not in model_map:
            model_map[key] = {
                "model": key[0],
                "provider": key[1],
                "calls": 0,
                "tokens": 0,
                "cost": 0.0,
                "latencies": [],
            }
        m = model_map[key]
        m["calls"] += 1
        m["tokens"] += int(r.get("total_tokens") or 0)
        m["cost"] += display_cost_usd(r)
        m["latencies"].append(float(r.get("latency_ms") or 0))

    models = []
    for m in sorted(model_map.values(), key=lambda x: -x["calls"])[:10]:
        lats = m.pop("latencies")
        m["avg_latency"] = sum(lats) / len(lats) if lats else 0
        models.append(m)

    day_map: Dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "tokens": 0})
    for r in rows:
        ts = r.get("timestamp")
        day = str(ts)[:10] if ts is not None else ""
        if not day:
            continue
        day_map[day]["calls"] += 1
        day_map[day]["cost"] += display_cost_usd(r)
        day_map[day]["tokens"] += int(r.get("total_tokens") or 0)

    daily = [{"day": d, **v} for d, v in sorted(day_map.items())][-14:]

    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "avg_latency_ms": total_lat / total_calls if total_calls else 0,
        "error_count": error_count,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "error_rate_pct": (error_count / total_calls) * 100 if total_calls else 0,
        "models": models,
        "daily": daily,
    }
