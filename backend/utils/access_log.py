"""In-memory ring buffer of recent API requests (path + query, method, timestamp)."""
from collections import deque
from datetime import datetime, timezone
from typing import List

MAX_ENTRIES = 200
_ring: deque = deque(maxlen=MAX_ENTRIES)


def record_request(method: str, path: str, query: str) -> None:
    url = path
    if query:
        url = f"{path}?{query}"
    ts = datetime.now(timezone.utc).isoformat()
    _ring.appendleft({"method": method, "url": url, "timestamp": ts})


def get_entries() -> List[dict]:
    return list(_ring)
