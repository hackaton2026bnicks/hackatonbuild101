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
    intent          = COALESCE(EXCLUDED.intent, sessions.intent),
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
