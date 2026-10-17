"""Admin commands that run inside the API container, never over the public internet.

Usage (on the server, through infra/admin.sh):
    python -m api.admin create-key <user> [team]
    python -m api.admin list-keys
    python -m api.admin revoke-key <id>
"""

import os
import re
import sys

import psycopg

from api.auth import hash_key, new_key

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
USAGE = "Usage: python -m api.admin create-key <user> [team] | list-keys | revoke-key <id>"


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
    raise SystemExit(USAGE)


if __name__ == "__main__":
    print(run(sys.argv[1:], os.environ["DATABASE_URL"]))
