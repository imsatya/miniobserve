"""Shared request dependencies and auth helpers."""
import os
import re
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

import auth.auth as auth

_APP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


def get_app(request: Request) -> str:
    """Dependency: resolve API key to app_name. 401 when key is missing/invalid."""
    app_name, err = auth.require_app(request)
    if err is not None:
        raise HTTPException(status_code=401, detail=err)
    return app_name


def require_admin(request: Request) -> None:
    secret = (os.environ.get("MINIOBSERVE_ADMIN_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="not_found")
    auth_h = request.headers.get("Authorization") or ""
    if not auth_h.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="invalid or missing admin Authorization")
    if auth_h[7:].strip() != secret:
        raise HTTPException(status_code=401, detail="invalid admin secret")


def validate_operator_app_name(name: str) -> str:
    n = (name or "").strip()
    if not n or not _APP_NAME_RE.match(n):
        raise HTTPException(status_code=400, detail="invalid app_name")
    if n.startswith("mo_"):
        raise HTTPException(status_code=400, detail="app_name prefix mo_ is reserved")
    return n


class MintApiKeyBody(BaseModel):
    app_name: str
    label: Optional[str] = None


def truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def trial_mint_enabled() -> bool:
    return truthy_env("MINIOBSERVE_PUBLIC_TRIAL_MINT")


def client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip() or "unknown"
    if request.client:
        return request.client.host or "unknown"
    return "unknown"
