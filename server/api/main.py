"""Team server: event ingestion, sessions, the live feed and API key management."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api.auth import Principal, hash_key, new_key, require_admin, require_key
from api.broker import Broker
from api.db import Database
from api.models import Conversation, ConversationCreate, EventBatch, KeyRequest

KEEPALIVE_SECONDS = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = Database(os.environ["DATABASE_URL"])
    app.state.broker = Broker()
    await app.state.db.open()
    yield
    await app.state.db.close()


app = FastAPI(
    title="build101 api",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

Caller = Annotated[Principal, Depends(require_key)]


@app.get("/health")
async def health(request: Request, caller: Caller) -> dict:
    db_ok = await request.app.state.db.ping()
    return {
        "status": "ok",
        "service": "api",
        "version": os.environ.get("GIT_SHA", "dev"),
        "db": "ok" if db_ok else "down",
    }


@app.get("/me")
async def me(caller: Caller) -> dict:
    return {"user": caller.user, "team": caller.team}


@app.post("/events", status_code=202)
async def ingest(batch: EventBatch, caller: Caller, request: Request) -> dict:
    items = await request.app.state.db.insert_events(caller.team, caller.user, batch.events)
    for item in items:
        request.app.state.broker.publish(caller.team, item)
    return {"accepted": len(items)}


@app.get("/sessions")
async def sessions(
    caller: Caller, request: Request, active_minutes: Annotated[int, Query(ge=1, le=7 * 24 * 60)] = 60
) -> dict:
    rows = await request.app.state.db.list_sessions(caller.team, active_minutes)
    return {"sessions": rows}


@app.get("/sessions/{session_id}/events")
async def session_events(
    session_id: str, caller: Caller, request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> dict:
    rows = await request.app.state.db.session_events(caller.team, session_id, limit)
    return {"events": rows}


@app.post("/conversations", status_code=201, response_model=Conversation)
async def create_conversation(body: ConversationCreate, caller: Caller, request: Request) -> dict:
    return await request.app.state.db.create_conversation(body.usuario, body.mensaje)


@app.get("/conversations", response_model=list[Conversation])
async def conversations(
    caller: Caller,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    return await request.app.state.db.list_conversations(limit, offset)


@app.get("/conversations/{conversation_id}", response_model=Conversation)
async def conversation(conversation_id: int, caller: Caller, request: Request) -> dict:
    row = await request.app.state.db.get_conversation(conversation_id)
    if row is None:
        raise HTTPException(404, "Conversation not found.")
    return row


@app.get("/feed")
async def feed(caller: Caller, request: Request) -> StreamingResponse:
    """Server-sent events with every event your team ingests from now on."""
    broker: Broker = request.app.state.broker
    queue = broker.subscribe(caller.team)

    async def stream():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: agent_event\nid: {item['id']}\ndata: {json.dumps(item)}\n\n"
        finally:
            broker.unsubscribe(caller.team, queue)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


@app.post("/admin/keys", status_code=201, dependencies=[Depends(require_admin)])
async def create_key(body: KeyRequest, request: Request) -> dict:
    key = new_key()
    key_id = await request.app.state.db.create_key(body.user, body.team, hash_key(key))
    return {"id": key_id, "user": body.user, "team": body.team, "key": key}


@app.get("/admin/keys", dependencies=[Depends(require_admin)])
async def list_keys(request: Request) -> dict:
    return {"keys": await request.app.state.db.list_keys()}


@app.delete("/admin/keys/{key_id}", dependencies=[Depends(require_admin)])
async def revoke_key(key_id: int, request: Request) -> dict:
    if not await request.app.state.db.revoke_key(key_id):
        raise HTTPException(404, "No active key with that id.")
    return {"revoked": key_id}
