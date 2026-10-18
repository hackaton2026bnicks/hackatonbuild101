"""Postgres access: connection pool, schema and queries."""

from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from api.models import Event

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

SESSION_UPSERT = """
INSERT INTO sessions (team, session_id, user_name, agent, parent_session_id, started_at,
                      last_event_at, last_event_type, last_summary, intent, ended_at, event_count)
VALUES (%(team)s, %(session_id)s, %(user)s, %(agent)s, %(parent)s, %(ts)s,
        %(ts)s, %(type)s, %(summary)s, %(intent)s, %(ended_at)s, 1)
ON CONFLICT (team, session_id) DO UPDATE SET
    last_event_at   = GREATEST(sessions.last_event_at, EXCLUDED.last_event_at),
    last_event_type = EXCLUDED.last_event_type,
    last_summary    = EXCLUDED.last_summary,
    intent          = COALESCE(sessions.intent, EXCLUDED.intent),  -- the first real request is the task
    ended_at        = CASE WHEN EXCLUDED.last_event_type = 'session_start' THEN NULL
                           ELSE COALESCE(EXCLUDED.ended_at, sessions.ended_at) END,
    event_count     = sessions.event_count + 1
WHERE sessions.user_name = EXCLUDED.user_name
"""

EVENT_INSERT = """
INSERT INTO events (team, user_name, agent, session_id, parent_session_id, type, summary, payload, ts)
VALUES (%(team)s, %(user)s, %(agent)s, %(session_id)s, %(parent)s, %(type)s, %(summary)s, %(payload)s, %(ts)s)
RETURNING id, received_at
"""


class Database:
    def __init__(self, url: str):
        self.pool = AsyncConnectionPool(
            url, min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=30)
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> bool:
        try:
            async with self.pool.connection(timeout=2) as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def find_key(self, key_hash: str) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, user_name, team FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
                (key_hash,),
            )
            return await cur.fetchone()

    async def create_key(self, user: str, team: str, key_hash: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO api_keys (user_name, team, key_hash) VALUES (%s, %s, %s) RETURNING id",
                (user, team, key_hash),
            )
            return (await cur.fetchone())["id"]

    async def list_keys(self) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, user_name, team, created_at, revoked_at FROM api_keys ORDER BY id"
            )
            return await cur.fetchall()

    async def revoke_key(self, key_id: int) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (key_id,)
            )
            return cur.rowcount == 1

    async def insert_events(self, team: str, user: str, events: list[Event]) -> list[dict]:
        """Store a batch in one transaction and return the feed items to broadcast."""
        items = []
        async with self.pool.connection() as conn, conn.transaction():
            for event in events:
                ts = datetime.fromtimestamp(event.ts, UTC)
                row = {
                    "team": team,
                    "user": user,
                    "agent": event.agent,
                    "session_id": event.session_id,
                    "parent": event.parent_session_id,
                    "type": event.type,
                    "summary": event.summary(),
                    "intent": event.intent(),
                    "ended_at": ts if event.type == "session_end" else None,
                    "payload": Jsonb(event.payload),
                    "ts": ts,
                }
                cur = await conn.execute(EVENT_INSERT, row)
                inserted = await cur.fetchone()
                await conn.execute(SESSION_UPSERT, row)
                items.append(
                    {
                        "id": inserted["id"],
                        "user": user,
                        "agent": event.agent,
                        "session_id": event.session_id,
                        "type": event.type,
                        "summary": row["summary"],
                        "ts": ts.isoformat(),
                    }
                )
        return items

    async def list_sessions(self, team: str, active_minutes: int) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT session_id, user_name AS user, agent, parent_session_id, intent,
                       last_event_type, last_summary, started_at, last_event_at, ended_at, event_count
                FROM sessions
                WHERE team = %s AND last_event_at > now() - make_interval(mins => %s)
                ORDER BY last_event_at DESC
                """,
                (team, active_minutes),
            )
            return await cur.fetchall()

    async def session_events(self, team: str, session_id: str, limit: int) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, user_name AS user, agent, type, summary, payload, ts, received_at
                FROM events
                WHERE team = %s AND session_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (team, session_id, limit),
            )
            return await cur.fetchall()

    async def create_conversation(self, usuario: str, mensaje: str) -> dict:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO conversacion (usuario, mensaje)
                VALUES (%s, %s)
                RETURNING id, usuario, mensaje, "timestamp" AS timestamp
                """,
                (usuario, mensaje),
            )
            return await cur.fetchone()

    async def get_conversation(self, conversation_id: int) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, usuario, mensaje, "timestamp" AS timestamp
                FROM conversacion
                WHERE id = %s
                """,
                (conversation_id,),
            )
            return await cur.fetchone()

    async def list_conversations(self, limit: int, offset: int) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, usuario, mensaje, "timestamp" AS timestamp
                FROM conversacion
                ORDER BY "timestamp" DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return await cur.fetchall()

    async def touch_resources(
        self,
        team: str,
        user: str,
        session_id: str,
        repo: str,
        agent_id: str | None,
        items: list[tuple[str, str]],
    ) -> None:
        """Record what the session is touching now. Items are (kind, value) pairs."""
        async with self.pool.connection() as conn:
            for kind, value in items:
                await conn.execute(
                    """
                    INSERT INTO session_resources (team, session_id, kind, value, user_name, repo, agent_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (team, session_id, kind, value) DO UPDATE SET last_seen = now()
                    """,
                    (team, session_id, kind, value, user, repo, agent_id),
                )

    async def session_topics(self, team: str, session_id: str) -> list[str]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT kind || ':' || value AS topic FROM session_resources
                WHERE team = %s AND session_id = %s AND kind IN ('repo', 'host', 'file')
                """,
                (team, session_id),
            )
            return [row["topic"] for row in await cur.fetchall()]

    async def recent_editor(
        self, team: str, repo: str, file: str, session_id: str, minutes: int
    ) -> dict | None:
        """Another live session that edited this file recently, if any."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT r.session_id, r.user_name, r.last_seen, s.intent, s.agent
                FROM session_resources r
                JOIN sessions s ON s.team = r.team AND s.session_id = r.session_id
                WHERE r.team = %s AND r.repo = %s AND r.kind = 'edit' AND r.value = %s
                  AND r.session_id <> %s
                  AND r.last_seen > now() - make_interval(mins => %s)
                  AND s.ended_at IS NULL
                ORDER BY r.last_seen DESC
                LIMIT 1
                """,
                (team, repo, file, session_id, minutes),
            )
            return await cur.fetchone()

    async def sessions_in_checkout(
        self, team: str, user: str, workspace: str, session_id: str, minutes: int
    ) -> list[dict]:
        """The person's other live sessions whose shell worked in this checkout lately."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT r.session_id, s.intent, max(r.last_seen) AS last_seen
                FROM session_resources r
                JOIN sessions s ON s.team = r.team AND s.session_id = r.session_id
                WHERE r.team = %s AND r.user_name = %s AND r.kind = 'workspace' AND r.value = %s
                  AND r.session_id <> %s AND r.last_seen > now() - make_interval(mins => %s)
                  AND s.ended_at IS NULL
                GROUP BY r.session_id, s.intent
                ORDER BY last_seen DESC
                """,
                (team, user, workspace, session_id, minutes),
            )
            return await cur.fetchall()

    async def already_told(self, team: str, session_id: str, kinds: list[str], topic: str) -> bool:
        """Whether this session already got (or has pending) a message of these kinds on this topic."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT 1 FROM messages m
                WHERE m.team = %s AND m.kind = ANY (%s) AND %s = ANY (m.topics)
                  AND (m.to_session = %s
                       OR EXISTS (SELECT 1 FROM deliveries d WHERE d.message_id = m.id AND d.session_id = %s))
                LIMIT 1
                """,
                (team, kinds, topic, session_id, session_id),
            )
            return await cur.fetchone() is not None

    async def solved_hosts(self, team: str, session_id: str) -> list[str]:
        """Hosts this session failed against, then called successfully, and shared nothing about."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT DISTINCT h.host
                FROM events f, jsonb_array_elements_text(f.payload -> 'hosts') AS h(host)
                WHERE f.team = %(team)s AND f.session_id = %(session)s
                  AND f.payload ->> 'hook' = 'PostToolUseFailure'
                  AND EXISTS (
                      SELECT 1 FROM events ok
                      WHERE ok.team = f.team AND ok.session_id = f.session_id AND ok.id > f.id
                        AND ok.payload ->> 'hook' = 'PostToolUse' AND ok.payload -> 'hosts' ? h.host)
                  AND NOT EXISTS (
                      SELECT 1 FROM messages m
                      WHERE m.team = f.team AND m.from_session = f.session_id
                        AND m.kind IN ('discovery', 'alert', 'note', 'answer')
                        AND 'host:' || h.host = ANY (m.topics))
                ORDER BY h.host
                """,
                {"team": team, "session": session_id},
            )
            return [row["host"] for row in await cur.fetchall()]

    async def recent_host_users(self, team: str, host: str, session_id: str, minutes: int) -> list[dict]:
        """Other sessions that called this host lately, most recent first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT r.session_id, r.user_name, r.last_seen, s.intent
                FROM session_resources r
                JOIN sessions s ON s.team = r.team AND s.session_id = r.session_id
                WHERE r.team = %s AND r.kind = 'host' AND r.value = %s AND r.session_id <> %s
                  AND r.last_seen > now() - make_interval(mins => %s)
                ORDER BY r.last_seen DESC
                LIMIT 3
                """,
                (team, host, session_id, minutes),
            )
            return await cur.fetchall()

    async def session_intent(self, team: str, session_id: str) -> str | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT intent FROM sessions WHERE team = %s AND session_id = %s", (team, session_id)
            )
            row = await cur.fetchone()
            return row["intent"] if row else None

    async def create_message(
        self,
        team: str,
        kind: str,
        body: str,
        topics: list[str],
        from_user: str,
        from_session: str | None,
        priority: int,
        ttl_minutes: int,
        reply_to: int | None = None,
        to_session: str | None = None,
    ) -> dict:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO messages (team, kind, topics, body, from_user, from_session, priority, expires_at,
                                      reply_to, to_session)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s), %s, %s)
                RETURNING id, kind, topics, body, from_user, from_session, priority, created_at, expires_at,
                          reply_to, to_session
                """,
                (
                    team,
                    kind,
                    topics,
                    body,
                    from_user,
                    from_session,
                    priority,
                    ttl_minutes,
                    reply_to,
                    to_session,
                ),
            )
            return await cur.fetchone()

    async def pending_messages(self, team: str, session_id: str, topics: list[str], limit: int) -> list[dict]:
        """Live messages relevant to this session that it has not received yet.

        Addressed messages (answers) reach only their session; the rest are routed by topic.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT m.id, m.kind, m.body, m.from_user, m.created_at, m.reply_to
                FROM messages m
                WHERE m.team = %s
                  AND m.kind IN ('discovery', 'alert', 'note', 'question', 'answer', 'overlap', 'workspace')
                  AND m.expires_at > now()
                  AND m.from_session IS DISTINCT FROM %s
                  AND (m.to_session = %s
                       OR (m.to_session IS NULL AND (cardinality(m.topics) = 0 OR m.topics && %s::text[])))
                  AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.message_id = m.id AND d.session_id = %s)
                ORDER BY m.priority DESC, m.created_at DESC
                LIMIT %s
                """,
                (team, session_id, session_id, topics, session_id, limit),
            )
            return await cur.fetchall()

    async def record_delivery(
        self, message_id: int, team: str, session_id: str, hook: str, outcome: str
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO deliveries (message_id, team, session_id, hook, outcome)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (message_id, session_id) DO NOTHING
                """,
                (message_id, team, session_id, hook, outcome),
            )

    async def session_brief(self, team: str, session_id: str) -> dict:
        """What a subagent should know about its parent session."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT intent FROM sessions WHERE team = %s AND session_id = %s", (team, session_id)
            )
            session = await cur.fetchone() or {}
            cur = await conn.execute(
                """
                SELECT value FROM session_resources
                WHERE team = %s AND session_id = %s AND kind = 'edit'
                ORDER BY last_seen DESC LIMIT 8
                """,
                (team, session_id),
            )
            edited = [row["value"] for row in await cur.fetchall()]
            cur = await conn.execute(
                """
                SELECT summary FROM events
                WHERE team = %s AND session_id = %s AND payload ? 'error'
                ORDER BY id DESC LIMIT 3
                """,
                (team, session_id),
            )
            failures = [row["summary"] for row in await cur.fetchall()]
            cur = await conn.execute(
                """
                SELECT m.body, m.from_user FROM deliveries d JOIN messages m ON m.id = d.message_id
                WHERE d.team = %s AND d.session_id = %s AND d.outcome = 'injected'
                  AND m.kind IN ('discovery', 'alert', 'note', 'answer') AND m.expires_at > now()
                ORDER BY d.delivered_at DESC LIMIT 5
                """,
                (team, session_id),
            )
            received = await cur.fetchall()
        return {"intent": session.get("intent"), "edited": edited, "failures": failures, "received": received}

    async def live_session(self, team: str, user: str, repo: str) -> str | None:
        """The user's most recently active live session in a repo: who an MCP call comes from.

        The tool call's own PreToolUse hook lands just before the call, so that session is the newest.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT s.session_id FROM sessions s
                JOIN session_resources r ON r.team = s.team AND r.session_id = s.session_id
                WHERE s.team = %s AND s.user_name = %s AND r.kind = 'repo' AND r.value = %s
                  AND s.ended_at IS NULL AND s.last_event_at > now() - interval '1 hour'
                ORDER BY s.last_event_at DESC
                LIMIT 1
                """,
                (team, user, repo),
            )
            row = await cur.fetchone()
            return row["session_id"] if row else None

    async def get_message(self, team: str, message_id: int) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT id, kind, topics, body, from_user, from_session, created_at, expires_at
                FROM messages WHERE team = %s AND id = %s
                """,
                (team, message_id),
            )
            return await cur.fetchone()

    async def team_activity(self, team: str, repo: str, minutes: int) -> list[dict]:
        """Live sessions in a repo: what they are doing, files they edited and files they touched."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT s.session_id, s.user_name, s.intent, s.last_summary, s.last_event_at,
                       ARRAY(SELECT e.value FROM session_resources e
                             WHERE e.team = s.team AND e.session_id = s.session_id AND e.kind = 'edit'
                               AND e.last_seen > now() - make_interval(mins => %(minutes)s)
                             ORDER BY e.last_seen DESC) AS editing,
                       ARRAY(SELECT f.value FROM session_resources f
                             WHERE f.team = s.team AND f.session_id = s.session_id AND f.kind = 'file'
                               AND f.last_seen > now() - make_interval(mins => %(minutes)s)
                             ORDER BY f.last_seen DESC LIMIT 40) AS touching
                FROM sessions s
                WHERE s.team = %(team)s AND s.ended_at IS NULL
                  AND s.last_event_at > now() - make_interval(mins => %(minutes)s)
                  AND EXISTS (SELECT 1 FROM session_resources r
                              WHERE r.team = s.team AND r.session_id = s.session_id
                                AND r.kind = 'repo' AND r.value = %(repo)s)
                ORDER BY s.user_name, s.started_at, s.session_id
                """,
                {"team": team, "repo": repo, "minutes": minutes},
            )
            return await cur.fetchall()

    async def active_repos(self, team: str, minutes: int) -> list[dict]:
        """Repos with live sessions lately, busiest first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT r.value AS repo, count(DISTINCT r.session_id) AS sessions,
                       max(r.last_seen) AS last_seen
                FROM session_resources r
                JOIN sessions s ON s.team = r.team AND s.session_id = r.session_id
                WHERE r.team = %s AND r.kind = 'repo' AND s.ended_at IS NULL
                  AND r.last_seen > now() - make_interval(mins => %s)
                GROUP BY r.value
                ORDER BY max(r.last_seen) DESC
                """,
                (team, minutes),
            )
            return await cur.fetchall()

    async def open_questions(self, team: str, topics: list[str], limit: int) -> list[dict]:
        """Unexpired questions on these topics (or the whole team) that nobody answered yet."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT q.id, q.body, q.from_user, q.from_session, q.created_at
                FROM messages q
                WHERE q.team = %s AND q.kind = 'question' AND q.expires_at > now()
                  AND (cardinality(q.topics) = 0 OR q.topics && %s::text[])
                  AND NOT EXISTS (SELECT 1 FROM messages a WHERE a.reply_to = q.id)
                ORDER BY q.created_at DESC
                LIMIT %s
                """,
                (team, topics, limit),
            )
            return await cur.fetchall()

    async def answers_to(self, team: str, user: str, limit: int) -> list[dict]:
        """Live answers to questions this user asked, so they can be read even when hooks do not reach."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT a.id, a.reply_to, a.body, a.from_user, a.created_at, q.body AS question
                FROM messages a JOIN messages q ON q.id = a.reply_to
                WHERE a.team = %s AND a.kind = 'answer' AND q.from_user = %s AND a.expires_at > now()
                ORDER BY a.created_at DESC
                LIMIT %s
                """,
                (team, user, limit),
            )
            return await cur.fetchall()

    async def list_messages(self, team: str, include_expired: bool, limit: int) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT m.id, m.kind, m.topics, m.body, m.from_user, m.from_session,
                       m.created_at, m.expires_at, m.reply_to,
                       count(d.message_id) FILTER (WHERE d.outcome = 'injected') AS injected,
                       count(d.message_id) FILTER (WHERE d.outcome = 'holdout') AS holdout
                FROM messages m
                LEFT JOIN deliveries d ON d.message_id = m.id
                WHERE m.team = %s AND (%s OR m.expires_at > now())
                GROUP BY m.id
                ORDER BY m.created_at DESC
                LIMIT %s
                """,
                (team, include_expired, limit),
            )
            return await cur.fetchall()
