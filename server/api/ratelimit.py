"""In-memory rate limits, one sliding window per caller.

Requests with a bearer token are counted per token; every request also counts against its client IP,
so rotating fake tokens does not get around the limit. Good enough for a single API process.
"""

import hashlib
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import JSONResponse

WINDOW_SECONDS = 60
PER_KEY = 1200  # an agent fires a few hooks per second at most
PER_IP = 3000
PER_IP_WITHOUT_KEY = 120


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, bucket: str, limit: int, now: float) -> bool:
        hits = self._hits[bucket]
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def check(self, ip: str, token: str | None, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.allow(f"ip:{ip}", PER_IP, now):
            return False
        if token:
            return self.allow("key:" + hashlib.sha256(token.encode()).hexdigest()[:16], PER_KEY, now)
        return self.allow(f"anon:{ip}", PER_IP_WITHOUT_KEY, now)


async def rate_limit_middleware(request: Request, call_next):
    limiter: RateLimiter = request.app.state.rate_limiter
    authorization = request.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip, token):
        return JSONResponse(
            {"detail": "Too many requests. Slow down and retry in a minute."},
            status_code=429,
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
    return await call_next(request)
