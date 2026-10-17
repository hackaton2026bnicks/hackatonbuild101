"""Mock payments API with undocumented traps, used by the demo scenario.

For now it only exposes /health. The traps (short token TTL, required
Idempotency-Key, HTTP 200 with the error in the body) come next.
"""

import os

from fastapi import FastAPI

app = FastAPI(title="mock bank")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mockbank", "version": os.environ.get("GIT_SHA", "dev")}
