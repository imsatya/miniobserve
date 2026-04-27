#!/usr/bin/env python3
"""
Recompute cognitive_mode + mo_run_summaries for existing mo_llm_logs (after classifier upgrades or migration).

Usage:
  cd backend && MINIOBSERVE_BACKEND=supabase python3 backfill_cognitive.py
  cd backend && python3 backfill_cognitive.py --app doc-agent

Loads backend/.env if present (SUPABASE_URL, etc.).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    p = Path(__file__).resolve().parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(
        description="Backfill cognitive labels (modes + mo_run_summaries) for existing logs."
    )
    ap.add_argument("--app", help="Only this app_name (omit to process all apps in DB)")
    ap.add_argument(
        "--scan-limit",
        type=int,
        default=100_000,
        help="Max recent rows per app to scan when discovering run keys (default 100000)",
    )
    args = ap.parse_args()

    if not os.environ.get("MINIOBSERVE_BACKEND"):
        os.environ["MINIOBSERVE_BACKEND"] = "sqlite"

    import cognitive.run_compute as run_cognitive

    out = run_cognitive.backfill_cognitive_runs(app_name=args.app, scan_limit=args.scan_limit)
    print(
        f"apps={out['apps']} runs_recomputed={out['runs']} errors={len(out['errors'])}",
        flush=True,
    )
    for e in out["errors"][:20]:
        print(f"  ERR {e.get('app_name')} {e.get('run_key')}: {e.get('error')}", flush=True)
    if len(out["errors"]) > 20:
        print(f"  ... and {len(out['errors']) - 20} more errors", flush=True)
    if out["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
