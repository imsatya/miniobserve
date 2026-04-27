"""HTTP transport with retries and optional request correlation."""
from __future__ import annotations

import random
import time
import uuid
from typing import Any, Dict, Optional

import httpx

# Transient HTTP status codes worth retrying.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _merge_headers(base: dict[str, str], extra: Optional[dict[str, str]]) -> dict[str, str]:
    out = dict(base)
    if extra:
        out.update({k: v for k, v in extra.items() if v is not None})
    return out


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json_body: Any = None,
    headers: Optional[dict[str, str]] = None,
    max_retries: int = 4,
    base_delay_s: float = 0.25,
) -> tuple[Optional[httpx.Response], Optional[dict]]:
    """
    Perform JSON HTTP request with exponential backoff + jitter on retryable failures.

    Always sends X-MiniObserve-Request-Id (idempotency / support correlation).

    Returns (response_or_none, parsed_json_or_none_on_success).
    """
    rid = uuid.uuid4().hex
    hdrs = _merge_headers(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-MiniObserve-Request-Id": rid,
        },
        headers,
    )
    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        try:
            r = client.request(method, url, json=json_body, headers=hdrs, timeout=30.0)
            if r.status_code < 400:
                try:
                    return r, r.json()
                except Exception:
                    return r, None
            if r.status_code not in _RETRYABLE_STATUS or attempt >= max_retries:
                return r, None
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            last_exc = e
            if attempt >= max_retries:
                return None, None
        if attempt < max_retries:
            delay = base_delay_s * (2**attempt) + random.uniform(0, 0.1)
            time.sleep(delay)
    if last_exc:
        return None, None
    return None, None
