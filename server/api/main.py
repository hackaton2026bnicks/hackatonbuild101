"""Team server: event ingestion, lessons, search and the live feed.

For now it only exposes /health so the deploy pipeline can be verified end to end.
"""

import os

from fastapi import FastAPI

app = FastAPI(title="build101 api")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "api", "version": os.environ.get("GIT_SHA", "dev")}
