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

-- Team knowledge (docs/DIK-V2.md): stable, atomic units that outlive messages and reach sessions by situation.
CREATE TABLE IF NOT EXISTS knowledge_units (
    id              bigserial PRIMARY KEY,
    team            text NOT NULL,
    repo            text,
    type            text NOT NULL,       -- gotcha | receta | decision | dato | criterio | recurso
    title           text NOT NULL,
    norm_title      text NOT NULL,       -- for merging the same lesson learned twice
    body            text NOT NULL,
    dead_ends       text[] NOT NULL DEFAULT '{}',
    files           text[] NOT NULL DEFAULT '{}',
    hosts           text[] NOT NULL DEFAULT '{}',
    keywords        text[] NOT NULL DEFAULT '{}',
    confidence      text NOT NULL,       -- explicito | verificado | observado
    status          text NOT NULL,       -- candidata (only its author sees it) | activa | archivada
    author          text NOT NULL,
    source_session  text,
    message_id      bigint REFERENCES messages (id) ON DELETE SET NULL,
    confirmations   integer NOT NULL DEFAULT 1,
    tsv             tsvector NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS knowledge_units_tsv ON knowledge_units USING gin (tsv);
CREATE INDEX IF NOT EXISTS knowledge_units_files ON knowledge_units USING gin (files);
CREATE INDEX IF NOT EXISTS knowledge_units_hosts ON knowledge_units USING gin (hosts);
CREATE INDEX IF NOT EXISTS knowledge_units_team_status ON knowledge_units (team, status, repo);
CREATE INDEX IF NOT EXISTS knowledge_units_norm ON knowledge_units (team, norm_title);

-- What happened to each unit: injected or held out in a session, confirmed, and the feedback it got.
CREATE TABLE IF NOT EXISTS knowledge_events (
    ku_id       bigint NOT NULL REFERENCES knowledge_units (id) ON DELETE CASCADE,
    team        text NOT NULL,
    session_id  text NOT NULL DEFAULT '',
    user_name   text NOT NULL,
    event       text NOT NULL,           -- injected | holdout | confirmed | useful | wrong | stale | approve | discard
    ts          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ku_id, session_id, user_name, event)
);
