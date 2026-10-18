"""Team server: event ingestion, sessions, the live feed and API key management."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api import collab
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
    InsightsResult,
    KeyRequest,
    LessonCreate,
    LessonDone,
    MessageCreate,
)
from api.ratelimit import RateLimiter, rate_limit_middleware
from api.scrub import scrub
from api.vault import Vault

KEEPALIVE_SECONDS = 15
# Booting waits this long for the first read of the vault; after that it is refreshed in the background
VAULT_FIRST_LOAD_SECONDS = 20

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = Database(os.environ["DATABASE_URL"])
    app.state.broker = Broker()
    await app.state.db.open()
    app.state.vault = Vault.from_env()
    refresher = None
    if app.state.vault.configured:
        try:
            await asyncio.wait_for(asyncio.to_thread(app.state.vault.refresh), VAULT_FIRST_LOAD_SECONDS)
        except TimeoutError:
            log.warning("vault not loaded at start; it will be retried in the background")
        refresher = asyncio.create_task(app.state.vault.keep_fresh())
    yield
    if refresher:
        refresher.cancel()
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
        return await collab.handle(
            request.app.state.db, request.app.state.broker, caller, call, request.app.state.vault
        )
    except Exception:
        log.exception("hook processing failed")
        return {}


@app.get("/vault")
async def vault_notes(
    caller: Caller,
    request: Request,
    q: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    """The index of the team vault: its state, and the notes that best match q (all of them without q)."""
    vault: Vault = request.app.state.vault
    notes = vault.search(q, limit) if q else sorted(vault.notes.values(), key=lambda n: n.title)[:limit]
    return {"vault": vault.status(), "notes": [n.public() for n in notes]}


@app.get("/vault/note")
async def vault_note(caller: Caller, request: Request, title: Annotated[str, Query(max_length=300)]) -> dict:
    note = request.app.state.vault.get(title)
    if not note:
        raise HTTPException(404, "No vault note with that title.")
    return note.public(with_body=True)


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
        # Hot for whoever is working now; the archivista keeps it in the vault for whoever comes later
        files = [topic[5:] for topic in topics if topic.startswith("file:")]
        lesson = {"texto": message["body"], "archivos": files, "fuente": "share_discovery", "repo": body.repo}
        await db.create_lesson(caller.team, caller.user, lesson_row(lesson, session_id))
    return message


def lesson_row(fields: dict, session_id: str | None) -> dict:
    row = {"titulo": None, "tipo": None, "aplica_cuando": None, "archivos": [], "skill": None, "repo": None}
    row.update(fields)
    for name in ("texto", "titulo", "aplica_cuando"):
        if row[name]:
            row[name] = scrub(row[name])
    row["session_id"] = session_id
    return row


@app.post("/lessons", status_code=201)
async def create_lesson(body: LessonCreate, caller: Caller, request: Request) -> dict:
    """Queue a lesson for the archivista. The author is the key's owner, never what the client says."""
    db: Database = request.app.state.db
    session_id = body.session_id
    if not session_id and body.repo:
        session_id = await db.live_session(caller.team, caller.user, body.repo)
    fields = body.model_dump(exclude={"session_id"})
    return await db.create_lesson(caller.team, caller.user, lesson_row(fields, session_id))


@app.get("/lessons")
async def lessons(
    caller: Caller,
    request: Request,
    status: Annotated[Literal["pending", "filed", "discarded", "all"], Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict:
    rows = await request.app.state.db.list_lessons(caller.team, None if status == "all" else status, limit)
    return {"lessons": rows}


@app.post("/lessons/{lesson_id}/done")
async def lesson_done(lesson_id: int, body: LessonDone, caller: Caller, request: Request) -> dict:
    lesson = await request.app.state.db.finish_lesson(
        caller.team, lesson_id, caller.user, body.nota, body.descartada
    )
    if not lesson:
        raise HTTPException(404, "No lesson with that id.")
    return lesson


@app.post("/insights", status_code=201)
async def upload_insights(body: InsightsResult, caller: Caller, request: Request) -> dict:
    """A person's /insights result (resultado.json), uploaded after they reviewed it."""
    result = body.model_dump(mode="json")
    return await request.app.state.db.save_insights(caller.team, caller.user, result)


def shared_opportunities(results: list[dict]) -> list[dict]:
    """Opportunities two people or more found on the same topic: the ones worth building once for all."""
    by_topic: dict[str, dict] = {}
    for row in results:
        for item in row["data"].get("oportunidades") or []:
            topic = str(item.get("tema") or "").strip()
            if not topic:
                continue
            entry = by_topic.setdefault(
                topic, {"tema": topic, "personas": [], "tipos": [], "sesiones": 0, "horas": 0.0, "O": 0.0,
                        "que": []},
            )  # fmt: skip
            if row["persona"] not in entry["personas"]:
                entry["personas"].append(row["persona"])
            if item.get("tipo") and item["tipo"] not in entry["tipos"]:
                entry["tipos"].append(item["tipo"])
            for field in ("sesiones", "horas", "O"):
                try:
                    entry[field] += float(item.get(field) or 0)
                except (TypeError, ValueError):
                    pass
            entry["que"].append(
                {"persona": row["persona"], "que": item.get("que"), "trabajo": item.get("trabajo")}
            )
    shared = [e for e in by_topic.values() if len(e["personas"]) >= 2]
    return sorted(shared, key=lambda e: (len(e["personas"]), e["O"]), reverse=True)


@app.get("/insights")
async def insights(caller: Caller, request: Request) -> dict:
    """Each person's newest /insights result, and the opportunities they share."""
    results = await request.app.state.db.latest_insights(caller.team)
    return {"results": results, "shared": shared_opportunities(results)}


@app.get("/knowledge")
async def knowledge(
    caller: Caller, request: Request, minutes: Annotated[int, Query(ge=60, le=30 * 24 * 60)] = 7 * 24 * 60
) -> dict:
    """Does the team learn: what the vault holds, how many sessions it reached and what went into it."""
    stats = await request.app.state.db.knowledge_stats(caller.team, minutes)
    return {"vault": request.app.state.vault.status(), **stats}


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
