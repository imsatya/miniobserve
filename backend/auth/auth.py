"""API key auth: key from header only. Do not log Authorization or X-Api-Key."""
import os
from typing import Optional, Tuple

# OSS local default when MINIOBSERVE_API_KEYS is unset (see AGENTS.md).
LOCAL_DEFAULT_API_KEY = "sk-local-default-key"


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_env_key_map() -> dict[str, str]:
    """Parse MINIOBSERVE_API_KEYS only (no implicit key)."""
    raw = os.environ.get("MINIOBSERVE_API_KEYS", "").strip()
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            k, _, v = part.partition(":")
            out[k.strip()] = v.strip() or "default"
    return out


def _db_key_auth_enabled() -> bool:
    """When set, a presented API key must resolve via DB hash if env keys are empty."""
    return bool((os.environ.get("MINIOBSERVE_API_KEY_PEPPER") or "").strip())


def implicit_local_default_key_enabled() -> bool:
    """
    When True, effective_key_map() injects LOCAL_DEFAULT_API_KEY -> default if env keys are empty.
    """
    if _truthy_env("MINIOBSERVE_DISABLE_LOCAL_DEFAULT_KEY"):
        return False
    if (os.environ.get("MINIOBSERVE_ENV") or "").strip().lower() == "production":
        return False
    if (os.environ.get("MINIOBSERVE_API_KEYS") or "").strip():
        return False
    if _db_key_auth_enabled():
        return False
    return True


def effective_key_map() -> dict[str, str]:
    """
    Env MINIOBSERVE_API_KEYS map, or implicit {sk-local-default-key: default} for frictionless OSS local.
    """
    parsed = _parse_env_key_map()
    if parsed:
        return parsed
    if implicit_local_default_key_enabled():
        return {LOCAL_DEFAULT_API_KEY: "default"}
    return {}


def singleton_local_default_map(m: dict[str, str]) -> bool:
    """True when the only configured mapping is the documented OSS local default."""
    if len(m) != 1:
        return False
    k, v = next(iter(m.items()))
    return k == LOCAL_DEFAULT_API_KEY and v == "default"


def keys_configured() -> bool:
    return len(effective_key_map()) > 0


def get_app_from_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    env_map = effective_key_map()
    if key in env_map:
        return env_map[key]
    try:
        import auth.api_keys as akc

        return akc.resolve_app_from_presented_key(key)
    except Exception:
        return None


def get_key_from_request(request) -> Optional[str]:
    """Extract API key from Authorization: Bearer <key> or X-Api-Key. Do not log these headers."""
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Api-Key") or None


def require_app(request) -> Tuple[Optional[str], Optional[dict]]:
    """
    Returns (app_name, None) if valid, or (None, error_response_dict) for 401.
    When no effective env keys and DB-backed auth is not required for missing keys, returns ("default", None).
    """
    err = {
        "error": "invalid or missing API key",
        "detail": "Send Authorization: Bearer <key> or X-Api-Key header.",
    }
    eff = effective_key_map()
    key = get_key_from_request(request)

    if eff:
        if singleton_local_default_map(eff):
            if not key:
                return "default", None
            app = get_app_from_key(key)
            if app is None:
                return None, err
            return app, None
        if not key:
            return None, err
        app = get_app_from_key(key)
        if app is None:
            return None, err
        return app, None

    if key and _db_key_auth_enabled():
        app = get_app_from_key(key)
        if app is None:
            return None, err
        return app, None

    return "default", None
