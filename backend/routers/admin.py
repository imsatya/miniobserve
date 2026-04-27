import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

import utils.access_log as access_log
import auth.api_keys as api_key_credentials
import auth.trial as trial_rate_limit
from deps import (
    MintApiKeyBody,
    client_ip,
    get_app,
    require_admin,
    trial_mint_enabled,
    validate_operator_app_name,
)
from state import BACKEND

router = APIRouter()


@router.post("/api/admin/api-keys", dependencies=[Depends(require_admin)])
def mint_admin_api_key(body: MintApiKeyBody):
    app_name = validate_operator_app_name(body.app_name)
    raw = api_key_credentials.mint_raw_api_key()
    try:
        api_key_credentials.insert_credential(raw, app_name, label=body.label, source="admin")
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to store key: {e!s}") from e
    return {"api_key": raw, "app_name": app_name}


@router.post("/api/trial/api-keys")
def mint_trial_api_key(request: Request):
    if not trial_mint_enabled():
        raise HTTPException(status_code=404, detail="not_found")
    if not api_key_credentials.pepper_configured():
        raise HTTPException(status_code=503, detail="MINIOBSERVE_API_KEY_PEPPER must be set for trial key mint")
    ip = client_ip(request)
    if not trial_rate_limit.allow_trial_mint(ip):
        raise HTTPException(status_code=429, detail="rate limit exceeded; try again later")
    suffix = secrets.token_urlsafe(10).replace("-", "").replace("_", "")[:12]
    app_name = f"mo_{suffix}"
    raw = api_key_credentials.mint_raw_api_key()
    try:
        api_key_credentials.insert_credential(raw, app_name, label=None, source="minted")
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to store key: {e!s}") from e
    return {"api_key": raw, "app_name": app_name}


@router.get("/api/me")
def get_me(app_name: str = Depends(get_app)):
    return {"app_name": app_name}


@router.get("/api/access-log")
def get_access_log(app_name: str = Depends(get_app)):
    return {"entries": access_log.get_entries()}


@router.get("/api/backend")
def get_backend():
    return {"backend": BACKEND}


@router.get("/api/health")
def health_api():
    return {"status": "ok", "database": BACKEND}


@router.get("/health")
def health_root():
    return {"status": "ok", "database": BACKEND}


@router.get("/go/{app_name}")
def go_app(app_name: str, key: Optional[str] = Query(None)):
    q = f"app={app_name}"
    if key:
        q = f"{q}&key={key}"
    return RedirectResponse(url=f"/?{q}", status_code=302)
