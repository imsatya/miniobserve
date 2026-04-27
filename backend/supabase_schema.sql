-- MiniObserve: Idempotent schema — safe to run multiple times.
-- Dashboard → SQL editor → paste → Run
--
-- After running, set on your server:
--   MINIOBSERVE_BACKEND=supabase
--   SUPABASE_URL=https://<project>.supabase.co
--   SUPABASE_SERVICE_ROLE_KEY=eyJ...  (use service_role, not anon)

CREATE TABLE IF NOT EXISTS mo_llm_logs (
    id BIGSERIAL PRIMARY KEY,
    app_name TEXT DEFAULT 'default',
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt TEXT,
    response TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    latency_ms FLOAT DEFAULT 0,
    cost_usd FLOAT DEFAULT 0,
    error TEXT,
    run_id TEXT,
    span_name TEXT,
    parent_span_id BIGINT,
    cached_input_tokens BIGINT DEFAULT 0,
    span_type TEXT,
    cognitive_mode TEXT,
    cognitive_stuck BOOLEAN DEFAULT false,
    cognitive_waiting BOOLEAN DEFAULT false,
    messages JSONB,
    metadata JSONB DEFAULT '{}',
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mo_llm_logs_timestamp ON mo_llm_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_mo_llm_logs_model     ON mo_llm_logs(model);
CREATE INDEX IF NOT EXISTS idx_mo_llm_logs_app       ON mo_llm_logs(app_name);

-- Denormalized run summaries for fast list + trace UI (updated at ingest).
CREATE TABLE IF NOT EXISTS mo_run_summaries (
    app_name           TEXT NOT NULL,
    run_key            TEXT NOT NULL,
    mode_fractions     JSONB DEFAULT '{}',
    fingerprint_segments JSONB DEFAULT '[]',
    stuck_alerts       JSONB DEFAULT '[]',
    call_trace_segments JSONB DEFAULT '[]',
    PRIMARY KEY (app_name, run_key)
);

-- DB-backed API keys (hashed); requires MINIOBSERVE_API_KEY_PEPPER + admin mint route.
CREATE TABLE IF NOT EXISTS mo_api_key_credentials (
    id         BIGSERIAL PRIMARY KEY,
    key_hash   TEXT NOT NULL UNIQUE,
    app_name   TEXT NOT NULL,
    label      TEXT,
    source     TEXT NOT NULL DEFAULT 'admin',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mo_api_key_credentials_app ON mo_api_key_credentials(app_name);

-- PostgREST uses the anon/authenticated role for publishable keys; RLS would block inserts.
-- Prefer SUPABASE_SERVICE_ROLE_KEY on the backend — it bypasses RLS.
ALTER TABLE mo_llm_logs             DISABLE ROW LEVEL SECURITY;
ALTER TABLE mo_run_summaries        DISABLE ROW LEVEL SECURITY;
ALTER TABLE mo_api_key_credentials  DISABLE ROW LEVEL SECURITY;

-- Optional: RPC functions for efficient stats aggregation.
-- If skipped, the Python fallback computes stats client-side (fine for small datasets).

CREATE OR REPLACE FUNCTION miniobserve_stats(filter_app TEXT)
RETURNS TABLE (
    total_calls        BIGINT,
    total_tokens       BIGINT,
    total_cost_usd     FLOAT,
    avg_latency_ms     FLOAT,
    error_count        BIGINT,
    total_input_tokens  BIGINT,
    total_output_tokens BIGINT
) LANGUAGE sql AS $$
    SELECT
        COUNT(*)::BIGINT,
        COALESCE(SUM(total_tokens), 0)::BIGINT,
        COALESCE(SUM(cost_usd), 0),
        COALESCE(AVG(latency_ms), 0),
        COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0)::BIGINT,
        COALESCE(SUM(input_tokens), 0)::BIGINT,
        COALESCE(SUM(output_tokens), 0)::BIGINT
    FROM mo_llm_logs
    WHERE (filter_app = '' OR app_name = filter_app);
$$;

CREATE OR REPLACE FUNCTION miniobserve_models(filter_app TEXT)
RETURNS TABLE (
    model        TEXT,
    provider     TEXT,
    calls        BIGINT,
    tokens       BIGINT,
    cost         FLOAT,
    avg_latency  FLOAT
) LANGUAGE sql AS $$
    SELECT
        model, provider,
        COUNT(*)::BIGINT,
        COALESCE(SUM(total_tokens), 0)::BIGINT,
        COALESCE(SUM(cost_usd), 0),
        COALESCE(AVG(latency_ms), 0)
    FROM mo_llm_logs
    WHERE (filter_app = '' OR app_name = filter_app)
    GROUP BY model, provider
    ORDER BY COUNT(*) DESC
    LIMIT 10;
$$;

CREATE OR REPLACE FUNCTION miniobserve_daily(filter_app TEXT)
RETURNS TABLE (
    day    DATE,
    calls  BIGINT,
    cost   FLOAT,
    tokens BIGINT
) LANGUAGE sql AS $$
    SELECT
        timestamp::date AS day,
        COUNT(*)::BIGINT,
        COALESCE(SUM(cost_usd), 0),
        COALESCE(SUM(total_tokens), 0)::BIGINT
    FROM mo_llm_logs
    WHERE
        timestamp >= NOW() - INTERVAL '14 days'
        AND (filter_app = '' OR app_name = filter_app)
    GROUP BY day
    ORDER BY day ASC;
$$;

-- ---------------------------------------------------------------------------
-- Legacy upgrade: older installs used column `trace_id`; current code uses
-- `run_id`. Without this, PostgREST returns PGRST204 for inserts/selects.
-- Idempotent: no-op when `run_id` already exists or `trace_id` is absent.
-- ---------------------------------------------------------------------------
DO $migrate_run_id$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'mo_llm_logs' AND column_name = 'trace_id'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'mo_llm_logs' AND column_name = 'run_id'
  ) THEN
    ALTER TABLE public.mo_llm_logs RENAME COLUMN trace_id TO run_id;
  END IF;
END $migrate_run_id$;

-- Refresh PostgREST schema cache so the API sees the new column immediately.
NOTIFY pgrst, 'reload schema';
