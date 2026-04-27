"""MiniObserve Backend - FastAPI."""
import os
from pathlib import Path
from typing import List


def _load_dotenv() -> None:
    p = Path(__file__).resolve().parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            # Do not override vars already set (shell, tests, or conftest).
            os.environ.setdefault(key, val)


_load_dotenv()

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import utils.access_log as access_log
from routers.admin import router as admin_router
from routers.ingest import router as ingest_router
from routers.logs import router as logs_router
from routers.runs import router as runs_router
from state import db

db.init()


def _cors_allow_origins() -> List[str]:
    raw = (os.environ.get("MINIOBSERVE_CORS_ORIGINS") or "").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


_INGEST_FIELD_GUIDE = """
## Ingest contract (high level)

- **latency_ms**: Client-measured wall time for this span (same intent as “duration_ms”). The server does not infer this from `timestamp`.
- **input_tokens / output_tokens / cached_input_tokens**: From the LLM provider usage (or SDK). Optional cost fill server-side when counts + model are present.
- **Tool spans**: Use **span_type** `tool`; put tool output in **response** (and `metadata.tool_result` when using the Python Tracer). Do not infer tool output only from the next LLM `messages` row.
- **started_at / ended_at** (optional): ISO8601 strings on POST/PATCH; merged into **metadata** for UI/validation.

See repo **AGENTS.md** (Span fields the server cannot infer) for the full table.
""".strip()

app = FastAPI(
    title="MiniObserve",
    version="0.1.0",
    description=_INGEST_FIELD_GUIDE,
)


@app.exception_handler(httpx.ConnectError)
async def _httpx_connect_error(_request: Request, exc: httpx.ConnectError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "database_unreachable",
            "message": str(exc),
            "hint": (
                "Cannot reach Supabase (DNS or network). Check SUPABASE_URL / PUBLIC_SUPABASE_URL "
                "is https://<project-ref>.supabase.co, and that outbound HTTPS is allowed. "
                "Or set MINIOBSERVE_BACKEND=sqlite for local SQLite."
            ),
        },
    )


@app.exception_handler(httpx.TimeoutException)
async def _httpx_timeout(_request: Request, exc: httpx.TimeoutException):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "database_timeout",
            "message": str(exc),
            "hint": "Supabase request timed out - retry or check network.",
        },
    )


try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    _trusted = (os.environ.get("MINIOBSERVE_PROXY_TRUSTED_HOSTS") or "*").strip()
    _th = "*" if _trusted == "*" else [h.strip() for h in _trusted.split(",") if h.strip()]
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_th)
except ImportError:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

_MINIOBSERVE_PROD = (os.environ.get("MINIOBSERVE_ENV") or "").strip().lower() == "production"


@app.middleware("http")
async def asset_no_cache_when_not_production(request: Request, call_next):
    """index.html is no-store, but /assets/* used StaticFiles defaults — browsers could keep old JS."""
    response = await call_next(request)
    if _MINIOBSERVE_PROD:
        return response
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.on_event("startup")
def _production_checks() -> None:
    import auth.auth as auth

    env = (os.environ.get("MINIOBSERVE_ENV") or "").strip().lower()
    keys = (os.environ.get("MINIOBSERVE_API_KEYS") or "").strip()
    if env == "production" and not keys:
        print(
            "[miniobserve] WARNING: MINIOBSERVE_ENV=production but MINIOBSERVE_API_KEYS is empty - "
            "API runs in local mode (no auth). Set keys or unset MINIOBSERVE_ENV.",
            flush=True,
        )
    strict = (os.environ.get("MINIOBSERVE_FAIL_WITHOUT_API_KEYS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if strict and not keys:
        raise RuntimeError(
            "MINIOBSERVE_FAIL_WITHOUT_API_KEYS is set but MINIOBSERVE_API_KEYS is empty - refusing to start."
        )
    if auth.implicit_local_default_key_enabled():
        print(
            f"[miniobserve] Local default API key: {auth.LOCAL_DEFAULT_API_KEY} (app: default) — "
            "optional on requests; wrong Bearer returns 401; see AGENTS.md",
            flush=True,
        )


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    if request.url.path.startswith("/api"):
        access_log.record_request(request.method, request.url.path, request.url.query or "")
    return await call_next(request)


app.include_router(ingest_router)
app.include_router(admin_router)
app.include_router(runs_router)
app.include_router(logs_router)

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")
    _NO_CACHE_HTML = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        index = STATIC_DIR / "index.html"
        if index.exists():
            return HTMLResponse(content=index.read_text(encoding="utf-8"), headers=_NO_CACHE_HTML)
        return {"message": "MiniObserve API running. Frontend not built yet."}
else:

    @app.get("/")
    def root():
        return {"message": "MiniObserve API running", "docs": "/docs"}
