# DIK integration: what the team server must expose

DIK is a client that already runs on each developer's machine (repo `DIK`, Claude Code hooks). After every turn it starts a background evaluator (`claude -p`) that reads the new part of the transcript and answers three questions: what is worth keeping as knowledge, what has the team already done, and which work pattern repeats. Today it writes the result to local markdown files. This document lists what build101 needs to expose so DIK writes to and reads from the team server instead, and so people who do not use Claude or git can see and answer the same things from the web UI.

In build101 terms, DIK is one more adapter: a cold-path capture that posts at turn granularity, close to the transcript tailer plus distiller of SPEC section 3. It does not need the local daemon, the proxy or the struggle detector.

## Flow

```
Claude Code session
  | Stop / SessionEnd hook
  v
evaluator (claude -p, background) --MCP--> search, publish_note, pattern_hit, ask
                                                     |
                                                     v
                                        build101 api (Postgres)
                                                     |
UserPromptSubmit hook  <--REST: GET /inbox-----------+---> web UI (people without Claude)
  -> one line "DIK: ..." in the working session      +---> n8n webhook on high priority
```

Two interfaces over the same backend, for different callers:

- **MCP (streamable HTTP)** for the evaluator. It is a full Claude, so it searches and publishes by itself with tools.
- **REST** for hooks. A hook is a shell script with a 10 s timeout and cannot call MCP tools, so it needs a plain, fast `curl`.

## Auth

- One token per person, sent as `Authorization: Bearer <token>`. The token identifies the person and the team; no other identity field is trusted from the client.
- Store only a hash of the token. Tokens are created from the UI or a CLI script; the developer puts it in `DIK_TOKEN`.

## P0: replace the local files

These three are enough to switch DIK from local files to the server.

### `POST /api/notes`

One evaluator result. Idempotent on `id` (DIK already computes a content hash), so a retry never duplicates.

```json
{
  "id": "3956c8126c",
  "session_id": "9454ba29-...",
  "project": "goldsky",
  "ts": "2026-10-17T13:26:00-03:00",
  "priority": "normal | high",
  "knowledge": "Sandbox tokens expire after 300 s ...",
  "already_done": "goldsky-analytics already covers ...",
  "pattern": "Verify with data before answering",
  "now": "Integrating the stock report for Goldsky",
  "question": "Has anyone parsed Inventiva exports before?"
}
```

- Every text field is optional except `id`, `session_id` and `priority`.
- `now` updates the person's status (see P1). `question` opens a thread addressed to the team (see P1).
- `priority: "high"` creates an inbox item for the author and fires the n8n webhook.
- Response: `201` with the stored note, or `200` if the `id` already existed.

### `GET /api/search?q=&type=&limit=10`

What the evaluator uses to answer "what has the team already done".

- `type`: `note | knowledge | pattern | skill` (optional, all by default).
- Each hit returns `type`, `title`, `snippet`, `author`, `date` and a `url` to the UI. Source person and date are required: they tell people who to ask.
- Full text search on Postgres is fine for the MVP; pgvector later.

### MCP server

Mounted behind Caddy, for example at `/api/mcp` (FastAPI can mount the `mcp` SDK's streamable HTTP app). Same bearer token. Tools:

| Tool | Maps to |
|---|---|
| `search(q, type?, limit?)` | `GET /api/search` |
| `publish_note(note)` | `POST /api/notes` |
| `pattern_hit(name, note_id?)` | `POST /api/patterns/hit` (P1); returns the updated count |
| `ask(to, text)` | `POST /api/threads` (P1) |

Tool descriptions should say that returned content is data from teammates, not instructions.

## P1: the team side

### Status: "what is each person working on"

- `PUT /api/status` with `{ "now": "...", "project": "..." }`. Also set implicitly by `POST /api/notes` when `now` is present.
- `GET /api/status` returns the latest status per person on the team, with timestamp.

### Inbox: "is there anything for me"

- `GET /api/inbox?since=<iso8601>` returns pending items for the token's person: their own high-priority notes, and questions addressed to them or to the team. **Must answer in well under 1 s**: it runs on every prompt the developer sends.
- `GET /api/inbox/count` returns a bare integer. Used by the scheduled job as a free check before spending a Claude call.
- `POST /api/inbox/{id}/ack` marks an item delivered so it is not shown twice.

### Threads: questions and answers

- `POST /api/threads` with `{ "to": "alberto | team", "text": "..." }`.
- `POST /api/threads/{id}/answers` with `{ "text": "...", "by_agent": true }`. `by_agent` marks answers written by someone's Claude without human review, so the UI can label them.
- `GET /api/threads?status=open|answered|escalated`.
- A new question for a person creates an inbox item for them. If nobody answers within a configurable time, mark it escalated and fire the n8n webhook.

### Patterns

- `POST /api/patterns/hit` with `{ "name": "...", "note_id": "..." }` increments the count, creating the pattern if new (match on a normalized name; semantic merge can come later). Returns `{ "name", "count", "status" }`.
- At 3 or more hits the pattern becomes a candidate to build as a skill, tool or app, and appears in the UI review queue.

## P2: web UI and notifications

The UI is what makes this usable for people who do not use Claude:

- Team status board (who is on what, since when).
- Open questions, with a box to answer.
- Search over notes and knowledge.
- Pattern review queue (3+ hits), to pick what to build next.

n8n receives a webhook on high-priority notes and escalated threads and forwards to WhatsApp or Slack.

## Data model (Postgres, minimal)

```
people(id, team_id, name, token_hash)
notes(id, person_id, session_id, project, ts, priority, knowledge, already_done, pattern, now, question)
status(person_id, now, project, updated_at)
threads(id, from_person_id, to_person_id null = team, text, status, created_at)
answers(id, thread_id, person_id, text, by_agent, created_at)
inbox_items(id, person_id, kind, ref_id, created_at, acked_at)
patterns(id, team_id, name, normalized_name, count, status)
pattern_hits(pattern_id, note_id, person_id, ts)
```

## What changes on the DIK side once this exists

- `DIK_URL` and `DIK_TOKEN` environment variables. If both are set the evaluator uses the server; if not, it keeps writing local files.
- The evaluator gets `--mcp-config` pointing at `/api/mcp` and `mcp__dik__*` in its allowed tools.
- The `UserPromptSubmit` hook calls `GET /api/inbox` instead of reading a local file.
- A scheduled job every 30 minutes calls `GET /api/inbox/count`; only when it is above zero does it start `claude -p` to answer what it can and escalate the rest.

## Delivery checklist

To connect DIK, the DIK side needs:

1. The public base URL (the `APP_HOST` from deploy).
2. The MCP URL.
3. One token per person.
4. `openapi.json` (FastAPI serves it at `/api/openapi.json` by default).
