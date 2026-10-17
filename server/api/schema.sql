-- Applied on every API start; every statement must be idempotent.

CREATE TABLE IF NOT EXISTS api_keys (
    id          serial PRIMARY KEY,
    user_name   text NOT NULL,
    team        text NOT NULL,
    key_hash    text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

CREATE TABLE IF NOT EXISTS sessions (
    team               text NOT NULL,
    session_id         text NOT NULL,
    user_name          text NOT NULL,
    agent              text NOT NULL,
    parent_session_id  text,
    started_at         timestamptz NOT NULL,
    last_event_at      timestamptz NOT NULL,
    last_event_type    text NOT NULL,
    last_summary       text NOT NULL,
    intent             text,
    ended_at           timestamptz,
    event_count        integer NOT NULL DEFAULT 0,
    PRIMARY KEY (team, session_id)
);

CREATE INDEX IF NOT EXISTS sessions_team_last_event ON sessions (team, last_event_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id                 bigserial PRIMARY KEY,
    team               text NOT NULL,
    user_name          text NOT NULL,
    agent              text NOT NULL,
    session_id         text NOT NULL,
    parent_session_id  text,
    type               text NOT NULL,
    summary            text NOT NULL,
    payload            jsonb NOT NULL,
    ts                 timestamptz NOT NULL,
    received_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_team_session ON events (team, session_id, id);
CREATE INDEX IF NOT EXISTS events_team_received ON events (team, received_at DESC);

CREATE TABLE IF NOT EXISTS conversacion (
    id          bigserial PRIMARY KEY,
    usuario     text NOT NULL,
    mensaje     text NOT NULL,
    "timestamp" timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversacion_timestamp ON conversacion ("timestamp" DESC, id DESC);
