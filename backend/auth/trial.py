"""In-memory sliding-window rate limit for public trial key mint (per client IP)."""
import os
import threading
import time
from collections import deque

_lock = threading.Lock()
_mints: dict[str, deque] = {}

_WINDOW_SEC = 3600.0


def _max_per_hour() -> int:
    raw = (os.environ.get("MINIOBSERVE_TRIAL_MINT_PER_HOUR") or "8").strip()
    try:
        return max(1, min(100, int(raw)))
    except ValueError:
        return 8


def allow_trial_mint(client_ip: str) -> bool:
    now = time.time()
    limit = _max_per_hour()
    with _lock:
        q = _mints.setdefault(client_ip, deque())
        while q and q[0] < now - _WINDOW_SEC:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True
