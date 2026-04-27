"""Pytest: backend package on path + load backend/.env for integration tests."""
import os
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_env = _BACKEND / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

# Use a temp DB for SQLite tests so they never touch ~/.miniobserve/logs.db.
_tmp_db = tempfile.mktemp(suffix=".test.db")
os.environ.setdefault("MINIOBSERVE_DB", _tmp_db)
