"""Per-person API keys and the admin token.

Keys are random and long, so a plain SHA-256 is enough to store them: only the hash is kept.
"""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

KEY_PREFIX = "b101_"


@dataclass(frozen=True)
class Principal:
    key_id: int
    user: str
    team: str


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def require_key(request: Request, authorization: str | None = Header(default=None)) -> Principal:
    token = bearer_token(authorization)
    if not token:
        raise HTTPException(401, "Missing API key. Send it as 'Authorization: Bearer <key>'.")
    row = await request.app.state.db.find_key(hash_key(token))
    if not row:
        raise HTTPException(401, "Invalid or revoked API key.")
    return Principal(key_id=row["id"], user=row["user_name"], team=row["team"])


def require_admin(authorization: str | None = Header(default=None)) -> None:
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(503, "Admin API is disabled because ADMIN_TOKEN is not set.")
    token = bearer_token(authorization)
    if not token or not hmac.compare_digest(token, admin_token):
        raise HTTPException(401, "Invalid admin token.")
