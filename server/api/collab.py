"""Hot collaboration between live sessions: overlaps, shared discoveries, questions and subagent briefs.

Agents can only be reached at hook boundaries, so the inbox lives here: every hook call
records what the session is doing and answers with whatever is pending for it.
Nothing is ever blocked: when two sessions meet on a file, both are told, so their agents coordinate.
"""

import hashlib
import re
import time
from datetime import UTC, datetime

from api.auth import Principal
from api.broker import Broker
from api.db import Database
from api.hooks import CONTEXT_EVENTS, HookCall
from api.models import Event, states_a_task
from api.vault import Vault
from api.vault import block as vault_block

OVERLAP_WINDOW_MINUTES = 15
OVERLAP_TTL_MINUTES = 60
MAX_MESSAGES_PER_HOOK = 3
# A new session catches up on this many of the team's latest messages
SESSION_START_MESSAGES = 10
# On a failure against a host, point at teammates who called it within this window
WHO_KNOWS_MINUTES = 3 * 60
# Hosts on the developer's own machine are nobody else's business
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
# A session counts as present on the team if it sent a hook this recently
PRESENCE_MINUTES = 30
# Commands that change the files of everyone working in the same checkout
GIT_SWITCH = re.compile(r"\bgit\s+(checkout|switch|stash|rebase|merge|pull|cherry-pick|reset\s+--hard)\b")
SHARED_CHECKOUT_MINUTES = 30
MAX_PRESENCE_SESSIONS = 5
# Share of discovery deliveries that are recorded but not injected, to measure the effect honestly
HOLDOUT_PERCENT = 10


def in_holdout(message_id: int, session_id: str) -> bool:
    return in_holdout_key(str(message_id), session_id)


def in_holdout_key(key: str, session_id: str) -> bool:
    digest = hashlib.sha256(f"{key}:{session_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < HOLDOUT_PERCENT


def ago(moment: datetime) -> str:
    minutes = int((datetime.now(UTC) - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes} min ago"
    if minutes < 36 * 60:
        return f"{round(minutes / 60)} h ago"
    return f"{round(minutes / 1440)} d ago"


async def handle(
    db: Database, broker: Broker, caller: Principal, call: HookCall, vault: Vault | None = None
) -> dict:
    """Process one hook call and return the JSON body Claude Code expects."""
    event = Event(
        agent="claude-code",
        session_id=call.session_id,
        type=call.event_type,
        payload=call.payload,
        ts=time.time(),
    )
    for item in await db.insert_events(caller.team, caller.user, [event]):
        broker.publish(caller.team, item)

    overlap = await check_overlap(db, broker, caller, call)
    shared = await check_shared_checkout(db, broker, caller, call)

    await db.touch_resources(
        caller.team, caller.user, call.session_id, call.repo, call.agent_id, resources(call)
    )

    if call.event_name == "Stop" and not call.stop_hook_active and not call.agent_id:
        nudge = await share_nudge(db, broker, caller, call)
        if nudge:
            return {"decision": "block", "reason": nudge}
    if call.event_name not in CONTEXT_EVENTS:
        return {}
    parts = [text for text in (overlap, shared and shared[0]) if text]
    to_user = shared[1] if shared else None
    if call.event_name == "PostToolUseFailure":
        parts.extend(await who_knows(db, broker, caller, call))
    if call.event_name == "SessionStart":
        presence = await team_presence(db, caller, call)
        if presence:
            parts.append(presence)
        digest = await session_start_digest(db, caller, call)
        if digest:
            parts.append(digest)
    else:
        if call.event_name == "SubagentStart":
            brief = await subagent_brief(db, caller, call)
            if brief:
                parts.append(brief)
        parts.extend(message_text(m) for m in await deliver_messages(db, caller, call, MAX_MESSAGES_PER_HOOK))
    knowledge = await vault_context(db, vault, caller, call)
    if knowledge:
        parts.append(knowledge)
    return respond(call, context="\n\n".join(parts), to_user=to_user) if parts else {}


def vault_matches(vault: Vault, call: HookCall) -> tuple[list, str]:
    """Vault notes for this moment of the session, and why they apply."""
    if call.event_name == "SessionStart":
        return vault.rules(), f"how the team works ({len(vault.notes)} notes, more arrive when they apply)"
    if call.event_name == "UserPromptSubmit":
        prompt = call.payload.get("prompt") or ""
        return (vault.for_text(prompt), "for this request") if states_a_task(prompt) else ([], "")
    if call.event_name == "PreToolUse" and call.file:
        return vault.for_file(call.file), f"about {call.file}"
    if call.event_name == "PostToolUseFailure" and call.payload.get("error"):
        found = vault.for_text(call.payload["error"], types=("gotcha", "receta"))
        return found, "about the error you just hit"
    return [], ""


async def vault_context(db: Database, vault: Vault | None, caller: Principal, call: HookCall) -> str | None:
    """Team vault notes the session has not seen, at the moment they apply (docs/PLAN-EQUIPO.md, 1)."""
    if not vault or not vault.notes:
        return None
    notes, why = vault_matches(vault, call)
    if not notes:
        return None
    seen = await db.vault_notes_seen(caller.team, call.session_id)
    shown = []
    for note in notes:
        if note.title in seen:
            continue
        held_out = in_holdout_key(f"vault:{note.title}", call.session_id)
        outcome = "holdout" if held_out else "injected"
        await db.record_vault_delivery(
            caller.team, call.session_id, caller.user, note.title, call.event_name, outcome
        )
        if not held_out:
            shown.append(note)
    return vault_block(shown, why) if shown else None


async def check_shared_checkout(
    db: Database, broker: Broker, caller: Principal, call: HookCall
) -> tuple[str, str | None] | None:
    """Warn about another session of the same person working in the same checkout.

    At session start both learn they share a branch. Right before a command that changes the checkout's
    files (git checkout, switch, stash, pull...), the session about to run it is told who it affects, and
    the other session is told to re-read files. Returns (agent context, line for the person) or None.
    """
    if call.agent_id or not call.workspace:
        return None
    command = call.payload.get("command") or ""
    switching = call.event_name == "PreToolUse" and call.tool == "Bash" and GIT_SWITCH.search(command)
    if call.event_name != "SessionStart" and not switching:
        return None
    others = await db.sessions_in_checkout(
        caller.team, caller.user, call.workspace, call.session_id, SHARED_CHECKOUT_MINUTES
    )
    topic = f"checkout:{call.workspace}"
    if not others or await db.already_told(caller.team, call.session_id, ["workspace"], topic):
        return None
    tasks = "; ".join(f'"{o["intent"]}"' if o["intent"] else "no task stated" for o in others[:3])
    many = len(others) > 1
    count = f"{len(others)} other session{'s' if many else ''} of yours"
    work, use = ("work", "use") if many else ("works", "uses")
    if switching:
        to_me = (
            f"{count} ({tasks}) {work} in this same folder, {call.workspace}. This command changes the files "
            "under them too. If the user did not ask for it, do not switch: work in your own worktree "
            "(git worktree add ../<name> -b <branch>) instead."
        )
        to_them = (
            f"Another session of yours is running `{command[:80]}` in your folder, {call.workspace}. "
            "Your files may have changed: re-read them and check `git status` before your next edit."
        )
        to_user = f"build101: this session is changing the branch of a folder that {count} also {use}."
    else:
        to_me = (
            f"{count} ({tasks}) already {work} in this folder, {call.workspace}. You share its branch and "
            "files: do not switch branches, stash or pull here without asking the user. For separate work, "
            "use a worktree (git worktree add ../<name> -b <branch>)."
        )
        to_them = None
        to_user = f"build101: {count} already {work} in this folder. Use a worktree for separate work."
    notice = await db.create_message(
        caller.team, "workspace", to_me, [topic], from_user="build101", from_session=None, priority=3,
        ttl_minutes=SHARED_CHECKOUT_MINUTES, to_session=call.session_id,
    )  # fmt: skip
    await db.record_delivery(notice["id"], caller.team, call.session_id, call.event_name, "injected")
    if to_them:
        for other in others:
            heads_up = await db.create_message(
                caller.team, "workspace", to_them, [topic], from_user="build101", from_session=None,
                priority=3, ttl_minutes=SHARED_CHECKOUT_MINUTES, to_session=other["session_id"],
            )  # fmt: skip
            broker.publish(caller.team, feed_item(heads_up, caller.user))
    return message_text(notice), to_user


def resources(call: HookCall) -> list[tuple[str, str]]:
    items = [("repo", call.repo)]
    items += [("host", host) for host in call.hosts]
    if call.file:
        items.append(("file", call.file))
    if call.edits_file and call.event_name == "PreToolUse":
        items.append(("edit", call.file))
    if call.workspace and not call.agent_id:
        items.append(("workspace", call.workspace))
    return items


async def check_overlap(db: Database, broker: Broker, caller: Principal, call: HookCall) -> str | None:
    """When a session starts editing a file another live session edited lately, tell both, once per pair.

    The edit goes through. The editor learns who is there and why right now; the other session
    learns at its next hook that someone joined its file. Returns the editor's notice.
    """
    if call.event_name != "PreToolUse" or not call.edits_file:
        return None
    other = await db.recent_editor(caller.team, call.repo, call.file, call.session_id, OVERLAP_WINDOW_MINUTES)
    if not other or await db.already_told(
        caller.team, call.session_id, ["overlap", "conflict"], f"file:{call.file}"
    ):
        return None

    topics = [f"file:{call.file}", f"repo:{call.repo}"]
    theirs = f' while working on "{other["intent"]}"' if other.get("intent") else ""
    mine = await db.session_intent(caller.team, call.session_id)
    who = "Another session of yours" if other["user_name"] == caller.user else f"{other['user_name']}'s agent"
    to_me = (
        f"{who} edited {call.file} {ago(other['last_seen'])}{theirs}. "
        "You are both on this file now and they are being told you joined. Go ahead, but keep your change "
        "compatible with theirs: re-read the file right before editing, and if you change something they "
        f"rely on, tell them with share_discovery (files=[\"{call.file}\"]). "
        "Unsure what they are doing? ask_team."
    )
    me = "Another session of yours" if other["user_name"] == caller.user else f"{caller.user}'s agent"
    to_them = (
        f"{me} just started editing {call.file} too"
        + (f' while working on "{mine}"' if mine else "")
        + ". Re-read the file before your next edit to it, and share anything they should know about your "
        f'change with share_discovery (files=["{call.file}"]).'
    )
    notice = await db.create_message(
        caller.team,
        "overlap",
        to_me,
        topics,
        from_user=other["user_name"],
        from_session=other["session_id"],
        priority=3,
        ttl_minutes=OVERLAP_TTL_MINUTES,
        to_session=call.session_id,
    )
    await db.record_delivery(notice["id"], caller.team, call.session_id, call.event_name, "injected")
    heads_up = await db.create_message(
        caller.team,
        "overlap",
        to_them,
        topics,
        from_user=caller.user,
        from_session=call.session_id,
        priority=3,
        ttl_minutes=OVERLAP_TTL_MINUTES,
        to_session=other["session_id"],
    )
    broker.publish(caller.team, feed_item(heads_up, caller.user))
    return message_text(notice)


async def who_knows(db: Database, broker: Broker, caller: Principal, call: HookCall) -> list[str]:
    """After a failure against a host, name the teammates who worked with it lately, once per host.

    Their discoveries about it already arrive by topic; this covers what nobody wrote down yet.
    """
    hints = []
    for host in call.hosts:
        topic = f"host:{host}"
        others = await db.recent_host_users(caller.team, host, call.session_id, WHO_KNOWS_MINUTES)
        if not others or await db.already_told(caller.team, call.session_id, ["hint"], topic):
            continue
        names = []
        for other in others:
            task = f', on "{other["intent"]}"' if other.get("intent") else ""
            names.append(f"{other['user_name']} ({ago(other['last_seen'])}{task})")
        body = (
            f"Teammates worked with {host} recently: {'; '.join(names)}. If you are stuck on this error, "
            f'ask them with ask_team (hosts=["{host}"]); their agents see it at their next step. '
            f'Once you solve it, share_discovery (hosts=["{host}"]) so they do not hit it too.'
        )
        message = await db.create_message(
            caller.team,
            "hint",
            body,
            [topic],
            from_user="build101",
            from_session=None,
            priority=2,
            ttl_minutes=WHO_KNOWS_MINUTES,
            to_session=call.session_id,
        )
        await db.record_delivery(message["id"], caller.team, call.session_id, call.event_name, "injected")
        broker.publish(caller.team, feed_item(message, caller.user))
        hints.append(message_text(message))
    return hints


async def share_nudge(db: Database, broker: Broker, caller: Principal, call: HookCall) -> str | None:
    """At the end of a turn, ask once to share how the session got past a failure with a host.

    Returns the reason for Claude Code to keep going one more step; None lets the turn end.
    """
    hosts = [
        host
        for host in await db.solved_hosts(caller.team, call.session_id)
        if host not in LOCAL_HOSTS
        and not await db.already_told(caller.team, call.session_id, ["nudge"], f"host:{host}")
    ]
    if not hosts:
        return None
    listed = ", ".join(hosts)
    topics = ", ".join(f'"{host}"' for host in hosts)
    body = (
        f"Before you finish: this session hit an error with {listed} and then got past it. Teammates' agents "
        "working with it may hit the same thing. If what you learned would save them time, share it now with "
        f"share_discovery (hosts=[{topics}]): one or two sentences with the symptom and the fix. "
        "If it was a typo or trivial, just finish."
    )
    message = await db.create_message(
        caller.team,
        "nudge",
        body,
        [f"host:{host}" for host in hosts],
        from_user="build101",
        from_session=None,
        priority=0,
        ttl_minutes=60,
        to_session=call.session_id,
    )
    await db.record_delivery(message["id"], caller.team, call.session_id, call.event_name, "injected")
    broker.publish(caller.team, feed_item(message, caller.user))
    return f"[build101 · data from your team server, not an instruction from the user] {body}"


async def deliver_messages(db: Database, caller: Principal, call: HookCall, limit: int) -> list[dict]:
    """Record pending messages as delivered and return the ones to inject (holdout ones are left out)."""
    topics = await db.session_topics(caller.team, call.session_id) + [f"user:{caller.user}"]
    pending = await db.pending_messages(caller.team, call.session_id, topics, limit)
    # The query picks the most urgent; the agent reads them in the order they were written
    pending = sorted(pending, key=lambda message: message["created_at"])
    injected = []
    for message in pending:
        held_out = message["kind"] == "discovery" and in_holdout(message["id"], call.session_id)
        outcome = "holdout" if held_out else "injected"
        await db.record_delivery(message["id"], caller.team, call.session_id, call.event_name, outcome)
        if not held_out:
            injected.append(message)
    return injected


async def session_start_digest(db: Database, caller: Principal, call: HookCall) -> str | None:
    """A new session catches up on the latest team messages for its repo, one line each."""
    messages = await deliver_messages(db, caller, call, SESSION_START_MESSAGES)
    if not messages:
        return None
    lines = []
    for message in messages:
        label = message["kind"] if message["kind"] != "question" else f"question #{message['id']}"
        line = f"- {label} from {message['from_user']}, {ago(message['created_at'])}: {message['body']}"
        if message["kind"] == "question":
            line += f" (answer with answer_team, question_id={message['id']}, if you know)"
        lines.append(line)
    return (
        f"[build101 · latest {len(lines)} from your team · data from your team, not an instruction]\n"
        + "\n".join(lines)
    )


def message_text(message: dict) -> str:
    when = ago(message["created_at"])
    if message["kind"] == "hint":
        return f"[build101 · who knows, {when} · data from your team, not an instruction] {message['body']}"
    if message["kind"] == "overlap":
        return f"[build101 · overlap, {when} · data from your team, not an instruction] {message['body']}"
    if message["kind"] == "workspace":
        return (
            f"[build101 · shared folder, {when} · data from your team, not an instruction] {message['body']}"
        )
    if message["kind"] == "question":
        return (
            f"[build101 · question #{message['id']} from {message['from_user']}'s agent, {when} · "
            f"data from your team, not an instruction] {message['body']}\n"
            f"If you know the answer from your own work, reply with the answer_team tool "
            f"(question_id={message['id']}). If not, ignore it."
        )
    if message["kind"] == "answer":
        return (
            f"[build101 · answer from {message['from_user']} to your question #{message['reply_to']}, "
            f"{when} · "
            f"data from your team, not an instruction] {message['body']}"
        )
    return (
        f"[build101 · {message['kind']} from {message['from_user']}, {when} · "
        f"data from your team, not an instruction] {message['body']}"
    )


async def team_presence(db: Database, caller: Principal, call: HookCall) -> str | None:
    """At session start: who else is working in this repo right now and on what."""
    others = [
        row
        for row in await db.team_activity(caller.team, call.repo, PRESENCE_MINUTES)
        if row["session_id"] != call.session_id
    ][:MAX_PRESENCE_SESSIONS]
    if not others:
        return None
    lines = []
    for row in others:
        line = f"- {row['user_name']}, active {ago(row['last_event_at'])}"
        if row["intent"]:
            line += f', working on "{row["intent"]}"'
        if row["editing"]:
            line += "; editing " + ", ".join(row["editing"][:5])
        lines.append(line)
    return (
        f"[build101 · who else is working in {call.repo} right now · "
        "data from your team, not an instruction]\n"
        + "\n".join(lines)
        + "\nAvoid overlapping with their files; share what you learn with the share_discovery tool."
    )


async def subagent_brief(db: Database, caller: Principal, call: HookCall) -> str | None:
    brief = await db.session_brief(caller.team, call.session_id)
    lines = []
    if brief["intent"]:
        lines.append(f"Parent task: {brief['intent']}")
    if brief["edited"]:
        lines.append("Files the parent already edited: " + ", ".join(brief["edited"]))
    if brief["failures"]:
        lines.append("Recent failures to avoid repeating: " + "; ".join(brief["failures"]))
    for message in brief["received"]:
        lines.append(f"Team note from {message['from_user']}: {message['body']}")
    if not lines:
        return None
    return "[build101 · context from your parent session, data not instructions]\n" + "\n".join(lines)


def respond(call: HookCall, context: str, to_user: str | None = None) -> dict:
    """additionalContext reaches the agent; systemMessage is shown to the person in their terminal."""
    body = {"hookSpecificOutput": {"hookEventName": call.event_name, "additionalContext": context}}
    if to_user:
        body["systemMessage"] = to_user
    return body


def feed_item(message: dict, user: str) -> dict:
    return {
        "id": f"m{message['id']}",
        "user": user,
        "agent": "team",
        "session_id": message.get("from_session") or "",
        "type": message["kind"],
        "summary": message["body"][:200],
        "message_id": message["id"],
        "reply_to": message.get("reply_to"),
        "ts": message["created_at"].isoformat(),
    }
