"""Hashing and persistence for DB-backed API keys (admin / trial mint)."""
import hashlib
import hmac
import os
import secrets
from typing import Optional


def _pepper_bytes() -> Optional[bytes]:
    p = (os.environ.get("MINIOBSERVE_API_KEY_PEPPER") or "").strip()
    return p.encode("utf-8") if p else None


def pepper_configured() -> bool:
    return _pepper_bytes() is not None


def hash_api_key(raw_key: str) -> Optional[str]:
    """Return HMAC-SHA256 hex digest, or None if pepper is not configured."""
    p = _pepper_bytes()
    if not p:
        return None
    return hmac.new(p, raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_raw_api_key() -> str:
    return "sk_mo_" + secrets.token_urlsafe(32)


def _db_mod():
    backend = (os.environ.get("MINIOBSERVE_BACKEND") or "sqlite").strip().lower()
    if backend == "supabase":
        import db.supabase as mod
    else:
        import db.sqlite as mod
    return mod


def resolve_app_from_presented_key(raw_key: str) -> Optional[str]:
    h = hash_api_key(raw_key)
    if not h:
        return None
    return _db_mod().resolve_api_key_app_name(h)


def insert_credential(
    raw_key: str, app_name: str, *, label: Optional[str] = None, source: str = "admin"
) -> int:
    h = hash_api_key(raw_key)
    if not h:
        raise ValueError("MINIOBSERVE_API_KEY_PEPPER must be set to store API keys")
    return _db_mod().insert_api_key_credential(h, app_name, label=label, source=source)
