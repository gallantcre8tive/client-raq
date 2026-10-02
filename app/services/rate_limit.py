"""Simple in-memory rate limit (per process). Good enough for single Render instance."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

_lock = Lock()
_buckets: dict[str, deque] = defaultdict(deque)


def allow(key: str, *, limit: int = 10, window: int = 600) -> bool:
    """Return True if under limit for key within window seconds."""
    now = time.time()
    with _lock:
        q = _buckets[key]
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True
