"""Request and response models. Event follows the normalized schema in docs/SPEC.md section 3."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_BATCH = 500
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
