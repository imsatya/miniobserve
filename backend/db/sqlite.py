"""SQLite database backend."""
import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from db.tables import TABLE_API_KEY_CREDENTIALS, TABLE_LLM_LOGS, TABLE_RUN_SUMMARIES

# Must match log_ingest.MINIOBSERVE_CLIENT_SPAN_META_KEY (opaque span id stored in metadata JSON).
_CLIENT_SPAN_META_KEY = "miniobserve_client_span_id"

DB_PATH = os.environ.get("MINIOBSERVE_DB", str(Path.home() / ".miniobserve" / "logs.db"))
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

_db_file = Path(DB_PATH)
if _db_file.exists() and not os.access(DB_PATH, os.W_OK):
    import sys
    print(
        f"\n[miniobserve] ERROR: database file is not writable: {DB_PATH}\n"
        f"  Fix with: chmod 644 {DB_PATH}\n",
        file=sys.stderr,
    )
    sys.exit(1)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    t_log = TABLE_LLM_LOGS
    t_rs = TABLE_RUN_SUMMARIES
    t_key = TABLE_API_KEY_CREDENTIALS
    with get_conn() as conn:
        conn.execute(
            f"""
        CREATE TABLE IF NOT EXISTS {t_log} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT DEFAULT 'default',
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            prompt TEXT,
            response TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            error TEXT,
            run_id TEXT,
            span_name TEXT,
            parent_span_id INTEGER,
            cached_input_tokens INTEGER DEFAULT 0,
            span_type TEXT,
            cognitive_mode TEXT,
            cognitive_stuck INTEGER DEFAULT 0,
            cognitive_waiting INTEGER DEFAULT 0,
            messages TEXT,
            metadata TEXT DEFAULT '{{}}',
            timestamp TEXT NOT NULL
        )
        """
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_mo_timestamp ON {t_log}(timestamp)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_mo_model ON {t_log}(model)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_mo_app ON {t_log}(app_name)")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {t_rs} (
                app_name TEXT NOT NULL,
                run_key TEXT NOT NULL,
                mode_fractions TEXT,
                fingerprint_segments TEXT,
                stuck_alerts TEXT,
                call_trace_segments TEXT,
                PRIMARY KEY (app_name, run_key)
            )
        """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {t_key} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_hash TEXT NOT NULL UNIQUE,
                app_name TEXT NOT NULL,
                label TEXT,
                source TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_mo_api_key_credentials_app ON {t_key}(app_name)"
        )
        conn.commit()


def insert_log(row: dict) -> int:
    """Insert one row; returns new row id."""
    md = row.get("metadata")
    if not isinstance(md, dict):
        md = {}
    msgs = row.get("messages")
    msgs_json = json.dumps(msgs) if isinstance(msgs, list) and msgs else None
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO {t}
            (app_name, model, provider, prompt, messages, response, input_tokens, cached_input_tokens, output_tokens,
             total_tokens, latency_ms, cost_usd, error, run_id, span_name, parent_span_id, span_type, cognitive_mode,
             cognitive_stuck, cognitive_waiting, metadata, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                row["app_name"],
                row["model"],
                row["provider"],
                row["prompt"],
                msgs_json,
                row["response"],
                row["input_tokens"],
                int(row.get("cached_input_tokens") or 0),
                row["output_tokens"],
                row["total_tokens"],
                row["latency_ms"],
                row["cost_usd"],
                row["error"],
                row["run_id"],
                row["span_name"],
                row.get("parent_span_id"),
                row.get("span_type"),
                row.get("cognitive_mode"),
                1 if row.get("cognitive_stuck") else 0,
                1 if row.get("cognitive_waiting") else 0,
                json.dumps(md),
                row["timestamp"],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_log_row(app_name: str, log_id: int, updates: dict) -> bool:
    """Merge updates into an existing row (same app only). Returns True if updated."""
    if not updates:
        return False
    allowed = {
        "model",
        "provider",
        "prompt",
        "response",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "cost_usd",
        "error",
        "run_id",
        "span_name",
        "parent_span_id",
        "span_type",
        "cognitive_mode",
        "metadata",
        "timestamp",
    }
    sets = []
    vals = []
    for k, v in updates.items():
        if k not in allowed:
            continue
        if k == "metadata" and isinstance(v, dict):
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return False
    vals.extend([app_name, log_id])
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE {t} SET {', '.join(sets)} WHERE app_name = ? AND id = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount > 0


def query_logs(*, limit, offset, model, provider, app_name, has_error, search):
    filters, params = [], []
    if model:
        filters.append("model = ?")
        params.append(model)
    if provider:
        filters.append("provider = ?")
        params.append(provider)
    if app_name:
        filters.append("app_name = ?")
        params.append(app_name)
    if has_error is True:
        filters.append("error IS NOT NULL")
    elif has_error is False:
        filters.append("error IS NULL")
    if search:
        filters.append("(prompt LIKE ? OR response LIKE ? OR model LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {t} {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM {t} {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return total, [_deserialize_row(dict(r)) for r in rows]


def _deserialize_row(row: dict) -> dict:
    """Parse JSON-stored columns (messages, metadata) back to Python objects."""
    if row.get("messages") and isinstance(row["messages"], str):
        try:
            row["messages"] = json.loads(row["messages"])
        except Exception:
            row["messages"] = None
    if row.get("metadata") and isinstance(row["metadata"], str):
        try:
            row["metadata"] = json.loads(row["metadata"])
        except Exception:
            row["metadata"] = {}
    return row


def fetch_log(log_id: int):
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        row = conn.execute(f"SELECT * FROM {t} WHERE id = ?", (log_id,)).fetchone()
    return _deserialize_row(dict(row)) if row else None


def lookup_log_id_by_client_span(app_name: str, run_id: str, client_span_id: str) -> Optional[int]:
    """Find server row id for a prior span in the same run (opaque client id in metadata)."""
    if not app_name or not run_id or not client_span_id:
        return None
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            SELECT id, metadata FROM {t}
            WHERE app_name = ? AND IFNULL(run_id, '') = ?
            ORDER BY id ASC
            """,
            (app_name, run_id),
        )
        for r in cur.fetchall():
            md_raw = r["metadata"]
            try:
                md = json.loads(md_raw) if isinstance(md_raw, str) else (md_raw or {})
            except (json.JSONDecodeError, TypeError):
                md = {}
            if isinstance(md, dict) and md.get(_CLIENT_SPAN_META_KEY) == client_span_id:
                return int(r["id"])
    return None


def fetch_cost_estimate_rows(app_name: str):
    """All logs for an app with fields needed for pricing + stats (full table scan)."""
    where = "WHERE app_name = ?" if app_name else ""
    params = [app_name] if app_name else []
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT model, provider, input_tokens, output_tokens, cached_input_tokens,
                   cost_usd, latency_ms, error, total_tokens, timestamp
            FROM {t} {where}
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_stats(app_name):
    where = "WHERE app_name = ?" if app_name else ""
    params = [app_name] if app_name else []
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        agg = conn.execute(
            f"""
            SELECT
                COUNT(*) as total_calls,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cost_usd), 0) as total_cost_usd,
                COALESCE(AVG(latency_ms), 0) as avg_latency_ms,
                COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) as error_count,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens
            FROM {t} {where}
        """,
            params,
        ).fetchone()

        models = conn.execute(
            f"""
            SELECT model, provider, COUNT(*) as calls,
                   SUM(total_tokens) as tokens, SUM(cost_usd) as cost,
                   AVG(latency_ms) as avg_latency
            FROM {t} {where}
            GROUP BY model, provider ORDER BY calls DESC LIMIT 10
        """,
            params,
        ).fetchall()

        daily = conn.execute(
            f"""
            SELECT DATE(timestamp) as day, COUNT(*) as calls,
                   SUM(cost_usd) as cost, SUM(total_tokens) as tokens
            FROM {t} {where}
            GROUP BY day ORDER BY day DESC LIMIT 14
        """,
            params,
        ).fetchall()

    return dict(agg), [dict(m) for m in models], [dict(d) for d in reversed(daily)]


def delete_logs(app_name):
    t_log = TABLE_LLM_LOGS
    t_rs = TABLE_RUN_SUMMARIES
    with get_conn() as conn:
        if app_name:
            conn.execute(f"DELETE FROM {t_rs} WHERE app_name = ?", (app_name,))
            conn.execute(f"DELETE FROM {t_log} WHERE app_name = ?", (app_name,))
        else:
            conn.execute(f"DELETE FROM {t_rs}")
            conn.execute(f"DELETE FROM {t_log}")
        conn.commit()


def distinct_app_names() -> list:
    """All distinct app_name values in log table."""
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        rows = conn.execute(f"SELECT DISTINCT app_name FROM {t} ORDER BY app_name").fetchall()
    return [r[0] for r in rows if r[0] is not None]


def fetch_recent_logs(app_name: str, limit: int = 8000):
    """Recent logs for run aggregation (newest first)."""
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM {t} WHERE app_name = ? ORDER BY timestamp DESC LIMIT ?",
            (app_name, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_run_logs(app_name: str, run_key: str):
    """All steps for a run, oldest first."""
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        if run_key.startswith("orphan-"):
            try:
                oid = int(run_key.split("-", 1)[1])
            except ValueError:
                return []
            rows = conn.execute(
                f"SELECT * FROM {t} WHERE app_name = ? AND id = ? ORDER BY timestamp ASC",
                (app_name, oid),
            ).fetchall()
            return [_deserialize_row(dict(r)) for r in rows]
        rows = conn.execute(
            f"""
            SELECT * FROM {t} WHERE app_name = ? AND (
                json_extract(metadata, '$.run_id') = ?
                OR IFNULL(run_id, '') = ?
            )
            ORDER BY timestamp ASC
            """,
            (app_name, run_key, run_key),
        ).fetchall()
    return [_deserialize_row(dict(r)) for r in rows]


def batch_set_cognitive_modes(app_name: str, pairs: list) -> None:
    """pairs: list of (log_id, cognitive_mode, cognitive_stuck, cognitive_waiting)."""
    if not pairs:
        return
    t = TABLE_LLM_LOGS
    with get_conn() as conn:
        for item in pairs:
            log_id, mode, stuck, waiting = item
            conn.execute(
                f"""
                UPDATE {t}
                SET cognitive_mode = ?, cognitive_stuck = ?, cognitive_waiting = ?
                WHERE app_name = ? AND id = ?
                """,
                (mode, 1 if stuck else 0, 1 if waiting else 0, app_name, int(log_id)),
            )
        conn.commit()


def upsert_run_summary(app_name: str, run_key: str, data: dict) -> None:
    t = TABLE_RUN_SUMMARIES
    with get_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO {t} (app_name, run_key, mode_fractions, fingerprint_segments, stuck_alerts, call_trace_segments)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_name, run_key) DO UPDATE SET
                mode_fractions = excluded.mode_fractions,
                fingerprint_segments = excluded.fingerprint_segments,
                stuck_alerts = excluded.stuck_alerts,
                call_trace_segments = excluded.call_trace_segments
            """,
            (
                app_name,
                run_key,
                json.dumps(data.get("mode_fractions") or {}),
                json.dumps(data.get("fingerprint_segments") or []),
                json.dumps(data.get("stuck_alerts") or []),
                json.dumps(data.get("call_trace_segments") or []),
            ),
        )
        conn.commit()


def fetch_run_summaries_batch(app_name: str, run_keys: list) -> dict:
    """Return dict run_key -> summary row dict (parsed JSON fields)."""
    if not run_keys:
        return {}
    uniq = list(dict.fromkeys(run_keys))
    placeholders = ",".join("?" * len(uniq))
    t = TABLE_RUN_SUMMARIES
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM {t} WHERE app_name = ? AND run_key IN ({placeholders})",
            [app_name] + uniq,
        ).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        for k in ("mode_fractions", "fingerprint_segments", "stuck_alerts", "call_trace_segments"):
            raw = d.get(k)
            if isinstance(raw, str):
                try:
                    d[k] = json.loads(raw)
                except Exception:
                    d[k] = {} if k == "mode_fractions" else []
        out[d["run_key"]] = d
    return out


def insert_api_key_credential(
    key_hash: str, app_name: str, *, label: Optional[str] = None, source: str = "admin"
) -> int:
    t = TABLE_API_KEY_CREDENTIALS
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            INSERT INTO {t} (key_hash, app_name, label, source)
            VALUES (?, ?, ?, ?)
            """,
            (key_hash, app_name, label or None, source),
        )
        conn.commit()
        return int(cur.lastrowid)


def resolve_api_key_app_name(key_hash: str) -> Optional[str]:
    t = TABLE_API_KEY_CREDENTIALS
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT app_name FROM {t} WHERE key_hash = ? LIMIT 1",
            (key_hash,),
        ).fetchone()
    return row[0] if row else None
