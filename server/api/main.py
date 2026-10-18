"""Team server: event ingestion, sessions, the live feed and API key management."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api import collab, kb
from api.auth import Principal, hash_key, new_key, require_admin, require_key
from api.broker import Broker
from api.db import Database
from api.hooks import parse as parse_hook
from api.models import (
    DEFAULT_TTL_MINUTES,
    PRIORITY,
    Conversation,
    ConversationCreate,
    EventBatch,
    KeyRequest,
    KnowledgeCreate,
    KnowledgeFeedback,
    MessageCreate,
)
from api.ratelimit import RateLimiter, rate_limit_middleware
from api.scrub import scrub

KEEPALIVE_SECONDS = 15

log = logging.getLogger("api")


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
app.state.rate_limiter = RateLimiter()
app.middleware("http")(rate_limit_middleware)

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


@app.post("/hooks/claude-code")
async def claude_code_hook(
    caller: Caller,
    request: Request,
    x_build101_repo: Annotated[str | None, Header()] = None,
) -> dict:
    """Target of Claude Code HTTP hooks. Never fails the agent: on any internal error it answers {}."""
    try:
        raw = await request.json()
        call = parse_hook(raw, x_build101_repo) if isinstance(raw, dict) else None
        if call is None:
            return {}
        return await collab.handle(request.app.state.db, request.app.state.broker, caller, call)
    except Exception:
        log.exception("hook processing failed")
        return {}


@app.post("/messages", status_code=201)
async def publish_message(body: MessageCreate, caller: Caller, request: Request) -> dict:
    db: Database = request.app.state.db
    session_id = body.session_id
    if not session_id and body.repo:
        session_id = await db.live_session(caller.team, caller.user, body.repo)

    topics, to_session = body.topics, None
    if body.kind == "answer":
        question = await db.get_message(caller.team, body.reply_to)
        if not question or question["kind"] != "question":
            raise HTTPException(404, "No question with that id.")
        # The answer goes to whoever asked: their session, or else their newest one in the repo.
        # An asker with no live session gets it at their next one, and nobody else does.
        to_session = (
            question["from_session"]
            or (body.repo and await db.live_session(caller.team, question["from_user"], body.repo))
            or None
        )
        topics = question["topics"] if to_session else [f"user:{question['from_user']}"]
    elif not topics and body.repo and body.kind in ("discovery", "question"):
        # Agents publishing without topics mean their repo, not every team in the org
        topics = [f"repo:{body.repo}"]

    message = await db.create_message(
        caller.team,
        body.kind,
        scrub(body.body),
        topics,
        from_user=caller.user,
        from_session=session_id,
        priority=PRIORITY[body.kind],
        ttl_minutes=body.ttl_minutes or DEFAULT_TTL_MINUTES[body.kind],
        reply_to=body.reply_to,
        to_session=to_session,
    )
    request.app.state.broker.publish(caller.team, collab.feed_item(message, caller.user))
    if body.kind == "discovery":
        # A discovery is hot for its readers now and stays in the knowledge base for whoever comes later
        await kb.remember(db, caller.team, caller.user, discovery_unit(message, body.repo), session_id)
    return message


def discovery_unit(message: dict, repo: str | None) -> dict:
    text = message["body"].strip()
    first = text.split(". ")[0].rstrip(".")
    title = first if len(first) <= 120 else first[:117].rsplit(" ", 1)[0] + "..."
    topics = message["topics"]
    return {
        "type": "gotcha",
        "title": title,
        "body": text,
        "files": [t[5:] for t in topics if t.startswith("file:")],
        "hosts": [t[5:] for t in topics if t.startswith("host:")],
        "confidence": "explicito",
        "repo": repo,
        "message_id": message["id"],
    }


def clean_unit(body: KnowledgeCreate) -> dict:
    unit = body.model_dump()
    for field in ("title", "body"):
        unit[field] = scrub(unit[field])
    for field in ("dead_ends", "keywords"):
        unit[field] = [scrub(item) for item in unit[field]]
    unit["hosts"] = [host.strip().lower() for host in unit["hosts"]]
    return unit


@app.post("/kb", status_code=201)
async def kb_remember(body: KnowledgeCreate, caller: Caller, request: Request) -> dict:
    """Keep a lesson, or confirm the unit that already says the same."""
    db: Database = request.app.state.db
    session_id = body.session_id
    if not session_id and body.repo:
        session_id = await db.live_session(caller.team, caller.user, body.repo)
    unit, created = await kb.remember(db, caller.team, caller.user, clean_unit(body), session_id)
    return {"unit": unit, "created": created}


@app.get("/kb")
async def kb_list(
    caller: Caller,
    request: Request,
    q: Annotated[str | None, Query(max_length=500)] = None,
    repo: Annotated[str | None, Query(max_length=200)] = None,
    type: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """The team's units, newest first, or the ones that best match q. Candidates only for their author."""
    db: Database = request.app.state.db
    if q:
        units = await kb.search(db, caller.team, caller.user, q, repo, type, min(limit, 20))
    else:
        units = await kb.list_units(db, caller.team, caller.user, repo, limit)
    return {"units": units}


@app.get("/kb/candidates")
async def kb_candidates(caller: Caller, request: Request) -> dict:
    return {"units": await kb.candidates(request.app.state.db, caller.team, caller.user, 20)}


@app.get("/kb/{ku_id}")
async def kb_get(ku_id: int, caller: Caller, request: Request) -> dict:
    unit = await kb.get(request.app.state.db, caller.team, caller.user, ku_id)
    if not unit:
        raise HTTPException(404, "No knowledge unit with that id.")
    return unit


@app.post("/kb/{ku_id}/feedback")
async def kb_feedback(ku_id: int, body: KnowledgeFeedback, caller: Caller, request: Request) -> dict:
    db: Database = request.app.state.db
    session_id = body.session_id
    if not session_id and body.repo:
        session_id = await db.live_session(caller.team, caller.user, body.repo)
    try:
        unit = await kb.feedback(db, caller.team, caller.user, ku_id, body.verdict, session_id or "")
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None
    if not unit:
        raise HTTPException(404, "No knowledge unit with that id.")
    return unit


@app.get("/repos")
async def repos(
    caller: Caller, request: Request, active_minutes: Annotated[int, Query(ge=1, le=7 * 24 * 60)] = 24 * 60
) -> dict:
    """Repos your team has live sessions in, so a dashboard can pick one."""
    return {"repos": await request.app.state.db.active_repos(caller.team, active_minutes)}


@app.get("/team")
async def team(
    caller: Caller,
    request: Request,
    repo: Annotated[str, Query(min_length=1, max_length=200)],
    active_minutes: Annotated[int, Query(ge=1, le=24 * 60)] = collab.PRESENCE_MINUTES,
) -> dict:
    """Who is working in a repo right now, which questions wait for an answer, and answers to yours."""
    db: Database = request.app.state.db
    me = await db.live_session(caller.team, caller.user, repo)
    sessions = await db.team_activity(caller.team, repo, active_minutes)
    for row in sessions:
        row["you"] = row["session_id"] == me
    questions = await db.open_questions(caller.team, [f"repo:{repo}"], 10)
    answers = await db.answers_to(caller.team, caller.user, 10)
    return {"repo": repo, "sessions": sessions, "open_questions": questions, "answers_for_you": answers}


@app.get("/messages")
async def messages(
    caller: Caller,
    request: Request,
    include_expired: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    rows = await request.app.state.db.list_messages(caller.team, include_expired, limit)
    return {"messages": rows}


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
