# Contributing to MiniObserve

## Quick setup

1. Clone the repo and use Python 3.11 (or any 3.9+ runtime).
2. Install backend dependencies:
   - `cd backend`
   - `pip install -r requirements.txt`
   - `pip install pytest`
3. Run tests from `backend/`:
   - `pytest tests/ -q`
4. Optional frontend build check:
   - `cd frontend`
   - `npm ci`
   - `npm run build`

`backend/tests/test_supabase_db.py` includes integration tests that are skipped automatically when Supabase env vars are not configured.

## Backend module map

- `backend/main.py`: app shell only (startup checks, middleware, router registration, static serving).
- `backend/routers/ingest.py`: `POST /api/log` and `PATCH /api/log`.
- `backend/routers/runs.py`: run-level listing, run analysis, and run replay endpoints.
- `backend/routers/logs.py`: log listing/detail, replay-by-id, stats, and clear.
- `backend/routers/admin.py`: admin/trial key minting and health/access helpers.
- `backend/log_ingest.py`: request normalization/validation helpers for ingest.
- `backend/state.py`: backend selection (`sqlite` or `supabase`) and shared `db`.
