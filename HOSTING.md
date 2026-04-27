# Hosting MiniObserve

## What you ship

- **Backend** serves the API and the built UI from `backend/static` (run `npm run build` in `frontend/` before deploy, or use the provided **Dockerfile**).
- **`data/model_pricing.json`** must exist next to the repo layout (`backend/` + `data/`) so cost estimates work.

## TLS and one public URL

Put **one** HTTPS hostname in front of the app (Caddy, nginx, Traefik, cloud LB). Terminate TLS there and reverse-proxy to the app on port **7823**. The UI calls **`/api`** with relative URLs, so the browser and API must share the same **origin** (same scheme, host, and port).

## Environment

| Variable | Purpose |
|----------|---------|
| `MINIOBSERVE_BACKEND` | `sqlite` (default) or `supabase` |
| `MINIOBSERVE_DB` | SQLite file path; use a **mounted volume** in Docker (e.g. `/data/logs.db`) |
| `SUPABASE_URL` | Supabase project URL (also accepted as `PUBLIC_SUPABASE_URL`) |
| `SUPABASE_SERVICE_ROLE_KEY` | **Preferred** Supabase secret key — bypasses RLS, takes priority over `SUPABASE_KEY`. Use the "Secret key" from Dashboard → Settings → API. |
| `SUPABASE_KEY` | Fallback Supabase key. Must also be the service_role value; anon/publishable keys cause RLS 42501 errors on ingest. |
| `MINIOBSERVE_API_KEYS` | `key1:app1,key2:app2` — **set in production** so the API is not open |
| `MINIOBSERVE_ENV` | Set to `production` to log a warning if API keys are missing |
| `MINIOBSERVE_FAIL_WITHOUT_API_KEYS` | `1` / `true` — **refuse to start** if `MINIOBSERVE_API_KEYS` is empty |
| `MINIOBSERVE_CORS_ORIGINS` | Comma-separated origins, or omit / `*` for any origin (tighten if the UI is on another domain) |
| `MINIOBSERVE_PROXY_TRUSTED_HOSTS` | `*` (default) or comma list of reverse-proxy IPs/hostnames for `X-Forwarded-*` |
| `MINIOBSERVE_API_KEY_PEPPER` | (none) | Long random secret used to hash **minted** API keys at rest (`mo_api_key_credentials` table). Required to mint or authenticate DB-backed keys. |
| `MINIOBSERVE_ADMIN_SECRET` | (none) | If set, enables `POST /api/admin/api-keys` (Bearer this value) to mint new data keys for a chosen `app_name`. |
| `MINIOBSERVE_PUBLIC_TRIAL_MINT` | off | Set `1` / `true` only on a **public demo** host to allow `POST /api/trial/api-keys` without auth (isolated `trial_*` app per mint). |
| `MINIOBSERVE_TRIAL_MINT_PER_HOUR` | `8` | In-memory sliding-window cap per client IP for trial mint (single-worker; use an edge limiter for multi-worker). |

Secrets belong in the host environment or a secrets manager — **never** commit `backend/.env`.

### Minting API keys (self-host vs demo)

- **Static keys:** `MINIOBSERVE_API_KEYS=key:app_name` (no pepper required).
- **Minted keys:** set `MINIOBSERVE_API_KEY_PEPPER` and `MINIOBSERVE_ADMIN_SECRET`, run `backend/supabase_migration.sql` (includes `mo_api_key_credentials`) or use SQLite `init()`. Dashboard **Settings** can mint keys; plaintext is returned **once**.
- **Public trial:** enable only on a dedicated deployment with `MINIOBSERVE_PUBLIC_TRIAL_MINT=1`, TLS, and monitoring. Optional hardening: restrict `POST /api/trial/api-keys` at the reverse proxy.

Frontend build-time flag for the login “Get a trial API key” button: set `VITE_MINIOBSERVE_PUBLIC_TRIAL=true` when building the UI for a trial host (see `frontend/vite.config.js`).

## Health checks

- `GET /api/health` and `GET /health` — return `{"status":"ok","database":"sqlite"|"supabase"}` (no API key).

## Docker

```bash
docker compose build
docker compose up -d
```

For **Supabase**, set `MINIOBSERVE_BACKEND=supabase` and Supabase env vars in `docker-compose.yml` or `env_file`, and **omit** the SQLite volume if you do not need local SQLite.

Apply **`backend/supabase_migration.sql`** in the Supabase SQL editor once.

## Limits

- **`/api/access-log`** is an in-memory ring buffer: it resets when the process restarts and is not shared across multiple worker processes.
