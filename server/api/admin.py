"""Admin commands that run inside the API container, never over the public internet.

Usage (on the server, through infra/admin.sh):
    python -m api.admin create-key <user> [team]
    python -m api.admin list-keys
    python -m api.admin revoke-key <id>
    python -m api.admin repair-text [--apply]
"""

import json
import os
import re
import sys

import psycopg

from api.auth import hash_key, new_key

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
USAGE = (
    "Usage: python -m api.admin create-key <user> [team] | list-keys | revoke-key <id>"
    " | repair-text [--apply]"
)
# UTF-8 read as cp1252: "análisis" stored as "anÃ¡lisis", "¿" as "Â¿", "’" as "â€™"
MOJIBAKE = re.compile("[\u00c2\u00c3][\xa0-\xbf\u0152-\u2122]|\u00e2\u20ac")
# Text columns written from hook and MCP input, and the key that identifies each row
TEXT_COLUMNS = [
    ("messages", "id", "body"),
    ("sessions", "session_id", "intent"),
    ("sessions", "session_id", "last_summary"),
    ("events", "id", "summary"),
]


def unmangle(text: str) -> str:
    """The text as it was written, when it reached the server through a cp1252 stdin; else unchanged."""
    if not MOJIBAKE.search(text):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def unmangle_json(value):
    if isinstance(value, str):
        return unmangle(value)
    if isinstance(value, list):
        return [unmangle_json(item) for item in value]
    if isinstance(value, dict):
        return {key: unmangle_json(item) for key, item in value.items()}
    return value


def repair_text(conn, apply: bool) -> str:
    """Fix text stored before the hooks read stdin as UTF-8 (PR #22). Dry run unless apply."""
    lines, total = [], 0
    for table, key, column in TEXT_COLUMNS:
        rows = conn.execute(
            f"SELECT {key}, {column} FROM {table} WHERE {column} ~ '[ÂÃ]|â€'"  # noqa: S608 (fixed names)
        ).fetchall()
        fixed = [(row[0], unmangle(row[1])) for row in rows if unmangle(row[1]) != row[1]]
        if apply:
            for row_key, text in fixed:
                conn.execute(f"UPDATE {table} SET {column} = %s WHERE {key} = %s", (text, row_key))  # noqa: S608
        total += len(fixed)
        sample = f" e.g. {fixed[0][1][:60]!r}" if fixed else ""
        lines.append(f"{table}.{column}: {len(fixed)}{sample}")
    rows = conn.execute("SELECT id, payload FROM events WHERE payload::text ~ '[ÂÃ]|â€'").fetchall()
    fixed = [(row_id, unmangle_json(payload)) for row_id, payload in rows]
    fixed = [
        (row_id, payload) for (row_id, payload), (_, old) in zip(fixed, rows, strict=True) if payload != old
    ]
    if apply:
        for row_id, payload in fixed:
            conn.execute("UPDATE events SET payload = %s::jsonb WHERE id = %s", (json.dumps(payload), row_id))
    total += len(fixed)
    lines.append(f"events.payload: {len(fixed)}")
    verb = "Repaired" if apply else "Would repair (run again with --apply)"
    return "\n".join(lines + [f"{verb}: {total} values."])


def run(args: list[str], database_url: str) -> str:
    if not args:
        raise SystemExit(USAGE)
    command, rest = args[0], args[1:]
    with psycopg.connect(database_url) as conn:
        if command == "create-key" and 1 <= len(rest) <= 2:
            user, team = rest[0], rest[1] if len(rest) == 2 else "default"
            if not NAME.match(user) or not NAME.match(team):
                raise SystemExit("User and team may only contain letters, digits, '.', '_' and '-'.")
            key = new_key()
            key_id = conn.execute(
                "INSERT INTO api_keys (user_name, team, key_hash) VALUES (%s, %s, %s) RETURNING id",
                (user, team, hash_key(key)),
            ).fetchone()[0]
            return f"id={key_id} user={user} team={team}\nkey={key}\nThe key is shown only once."
        if command == "list-keys" and not rest:
            rows = conn.execute(
                "SELECT id, user_name, team, created_at, revoked_at FROM api_keys ORDER BY id"
            ).fetchall()
            lines = [
                f"{key_id}\t{user}\t{team}\tcreated {created:%Y-%m-%d %H:%M}"
                + (f"\trevoked {revoked:%Y-%m-%d %H:%M}" if revoked else "")
                for key_id, user, team, created, revoked in rows
            ]
            return "\n".join(lines) or "No keys yet."
        if command == "revoke-key" and len(rest) == 1 and rest[0].isdigit():
            updated = conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (int(rest[0]),)
            ).rowcount
            return f"Revoked key {rest[0]}." if updated else f"No active key with id {rest[0]}."
        if command == "repair-text" and rest in ([], ["--apply"]):
            return repair_text(conn, apply=rest == ["--apply"])
    raise SystemExit(USAGE)


if __name__ == "__main__":
    print(run(sys.argv[1:], os.environ["DATABASE_URL"]))
