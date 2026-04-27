"""Shared backend state: selected database backend and identifier."""
import os

BACKEND = os.environ.get("MINIOBSERVE_BACKEND", "sqlite").lower()

if BACKEND == "supabase":
    import db.supabase as db
else:
    import db.sqlite as db
