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

-- What each session is touching right now: its situation, used to route messages and spot overlaps.
CREATE TABLE IF NOT EXISTS session_resources (
    team        text NOT NULL,
    session_id  text NOT NULL,
    kind        text NOT NULL,           -- repo | host | file | edit
    value       text NOT NULL,
    user_name   text NOT NULL,
    repo        text NOT NULL,
    agent_id    text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team, session_id, kind, value)
);

CREATE INDEX IF NOT EXISTS session_resources_lookup ON session_resources (team, kind, value, last_seen DESC);

-- Messages between sessions: discoveries, alerts, notes, questions, answers and overlap notices.
CREATE TABLE IF NOT EXISTS messages (
    id            bigserial PRIMARY KEY,
    team          text NOT NULL,
    kind          text NOT NULL,
    topics        text[] NOT NULL DEFAULT '{}',
    body          text NOT NULL,
    from_user     text NOT NULL,
    from_session  text,
    priority      integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS messages_team_expires ON messages (team, expires_at DESC);
CREATE INDEX IF NOT EXISTS messages_topics ON messages USING gin (topics);

-- Answers point at their question and go only to the session that asked it
ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to bigint REFERENCES messages (id) ON DELETE CASCADE;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS to_session text;

-- One row per message and receiving session: what was injected, held out or skipped. Source of the metrics.
CREATE TABLE IF NOT EXISTS deliveries (
    message_id    bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    team          text NOT NULL,
    session_id    text NOT NULL,
    hook          text NOT NULL,
    outcome       text NOT NULL,         -- injected | holdout
    delivered_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, session_id)
);

-- Vault notes handed to a session by a hook (server/api/vault.py): never twice, and with a holdout to
-- measure whether they help. The note is its title, which is its file name in build101-vault.
CREATE TABLE IF NOT EXISTS vault_deliveries (
    team          text NOT NULL,
    session_id    text NOT NULL,
    user_name     text NOT NULL,
    note          text NOT NULL,
    hook          text NOT NULL,
    outcome       text NOT NULL,         -- injected | holdout
    delivered_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team, session_id, note)
);

-- Lessons waiting for the archivista (agent/hermes/bandeja.py), which runs where Hermes runs and files
-- them in build101-vault. They come from learn.py when a session ends and from share_discovery.
CREATE TABLE IF NOT EXISTS lessons (
    id             bigserial PRIMARY KEY,
    team           text NOT NULL,
    author         text NOT NULL,
    session_id     text,
    repo           text,
    texto          text NOT NULL,
    titulo         text,
    tipo           text,
    aplica_cuando  text,
    archivos       text[] NOT NULL DEFAULT '{}',
    fuente         text NOT NULL,
    status         text NOT NULL DEFAULT 'pending',   -- pending | filed | discarded
    nota           text,
    motivo         text,
    done_by        text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    done_at        timestamptz
);

ALTER TABLE lessons ADD COLUMN IF NOT EXISTS skill text;

CREATE INDEX IF NOT EXISTS lessons_queue ON lessons (team, status, id);
