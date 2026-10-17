"""Request and response models. Event follows the normalized schema in docs/SPEC.md section 3."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_BATCH = 500
MAX_MESSAGE_CHARS = 100_000
SUMMARY_CHARS = 200

EventType = Literal[
    "session_start",
    "intent",
    "plan",
    "pre_action",
    "post_action",
    "compact",
    "session_end",
    "user_correction",
    "subagent_start",
    "subagent_stop",
    "stop",
]

NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"


class Event(BaseModel):
    agent: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    parent_session_id: str | None = Field(default=None, max_length=128)
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float

    @field_validator("payload")
    @classmethod
    def payload_fits(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
        return value

    def summary(self) -> str:
        """One line describing the event for the feed. Prefers what the local agent already canonicalized."""
        p = self.payload
        for key in ("canonical", "summary"):
            if isinstance(p.get(key), str):
                return p[key][:SUMMARY_CHARS]
        if self.type in ("intent", "user_correction") and isinstance(p.get("prompt"), str):
            return p["prompt"][:SUMMARY_CHARS]
        if isinstance(p.get("tool"), str):
            return p["tool"][:SUMMARY_CHARS]
        return self.type

    def intent(self) -> str | None:
        if self.type == "intent" and isinstance(self.payload.get("prompt"), str):
            return self.payload["prompt"][:SUMMARY_CHARS]
        return None


class EventBatch(BaseModel):
    events: list[Event] = Field(min_length=1, max_length=MAX_BATCH)


class KeyRequest(BaseModel):
    user: str = Field(pattern=NAME_PATTERN)
    team: str = Field(default="default", pattern=NAME_PATTERN)


class ConversationCreate(BaseModel):
    usuario: str = Field(min_length=1, max_length=128)
    mensaje: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class Conversation(BaseModel):
    id: int
    usuario: str
    mensaje: str
    timestamp: datetime


MessageKind = Literal["discovery", "alert", "note"]
Topic = Annotated[str, Field(pattern=r"^(repo|host|file):\S{1,200}$")]

# Minutes a message stays deliverable when the publisher does not say
DEFAULT_TTL_MINUTES = {"discovery": 7 * 24 * 60, "alert": 60, "note": 24 * 60}
PRIORITY = {"alert": 2, "discovery": 1, "note": 0}


class MessageCreate(BaseModel):
    """Something one session or person shares with the sessions it is relevant to.

    Empty topics reach the whole team; otherwise only sessions whose situation shares a topic.
    """

    kind: MessageKind = "discovery"
    body: str = Field(min_length=1, max_length=1000)
    topics: list[Topic] = Field(default_factory=list, max_length=10)
    ttl_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    session_id: str | None = Field(default=None, max_length=128)
