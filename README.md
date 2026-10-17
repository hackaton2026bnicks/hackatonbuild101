# build101: situational memory for coding agents

Lessons that one coding agent learned the hard way are pushed to the next agent right before it makes the same mistake, even if that agent belongs to a teammate or comes from another vendor. Full design in [docs/SPEC.md](docs/SPEC.md).

## Layout

```
server/     team API: event ingestion, lessons, search, live feed (FastAPI)
worker/     background jobs: distiller and criteria miner
scenario/   mock bank with undocumented traps for the demo
ui/         team dashboard (static, served by Caddy)
infra/      docker compose, Caddy, AWS bootstrap and deploy scripts
tools/      helper scripts, such as a fake agent session for testing
docs/       spec
```

Coming next: `agent/` (local hook receiver, scrubber, canonicalizer, trigger index) and `adapters/`.

## Run locally

```bash
python -m venv .venv && source .venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt -r server/requirements.txt -r scenario/requirements.txt

# API tests need a Postgres; they are skipped without TEST_DATABASE_URL
docker run -d --rm --name b101-testdb -e POSTGRES_USER=build101 -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=build101 -p 55432:5432 pgvector/pgvector:pg16
export TEST_DATABASE_URL=postgresql://build101:test@localhost:55432/build101
ruff check . && pytest
```

Full stack with Docker (Postgres, Caddy and all services, built from source):

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml --env-file infra/.env.example up --build
```

Then open http://localhost. The API is under `/api`, the mock bank under `/bank`. Create a key with the local admin token and send a fake agent session to see the dashboard move:

```bash
curl -X POST http://localhost/api/admin/keys -H "Authorization: Bearer dev-admin-token" \
  -H "Content-Type: application/json" -d '{"user": "ana"}'
python tools/simulate_agents.py --url http://localhost/api --key b101_...
```

## API

Every API endpoint requires authentication. Use an API key as `Authorization: Bearer <key>` for health, conversations, events, sessions and the live feed. Admin key-management endpoints require the admin token instead. Interactive API documentation is disabled on the server.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/health` | Version and database status (API key required) |
| GET | `/api/me` | Who this key belongs to |
| POST | `/api/events` | Ingest a batch of normalized events (`{"events": [...]}`, max 500, 64 KB payload each) |
| GET | `/api/sessions?active_minutes=60` | Your team's recent sessions: who, which agent, intent, last action, state |
| GET | `/api/sessions/{id}/events` | Full events of one session, newest first |
| GET | `/api/feed` | Server-sent events stream of everything your team ingests from now on |
| POST/GET/DELETE | `/api/admin/keys` | Create, list and revoke keys (admin token only) |
| POST | `/api/conversations` | Save a conversation message (API key required) |
| GET | `/api/conversations?limit=50&offset=0` | List recent conversation messages (API key required) |
| GET | `/api/conversations/{id}` | Read one conversation message (API key required) |

Event schema: see [docs/SPEC.md](docs/SPEC.md) section 3. Put an already canonicalized action in `payload.canonical` and it becomes the line shown in the feed.

Conversation example:

```bash
curl -X POST https://<APP_HOST>/api/conversations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <API_KEY>" \
  -d '{"usuario":"ana","mensaje":"Texto largo del prompt..."}'

curl "https://<APP_HOST>/api/conversations?limit=50&offset=0" \
  -H "Authorization: Bearer <API_KEY>"
curl https://<APP_HOST>/api/conversations/1 \
  -H "Authorization: Bearer <API_KEY>"
```

The `mensaje` field accepts up to 100,000 characters. The database assigns `id` and `timestamp` automatically. HTTPS remains reachable from the Internet, but API routes reject requests without a valid API key (admin routes require the admin token). A valid API key currently grants access to all conversation records.

### Keys for teammates

On the deployed server, create one key per person (needs AWS CLI access to read the admin token):

```bash
./infra/create-key.sh ana
```

The key is shown once. Revoke one with `DELETE /api/admin/keys/{id}`.

## Deploy

Everything runs on one small EC2 instance (t3.small, about 20 USD/month if left on) with Docker Compose. Caddy terminates HTTPS with a free certificate on an `sslip.io` hostname.

```
push to main
  -> CI: ruff, pytest, bash -n
  -> log in to AWS with OIDC (no stored keys)
  -> build api, worker, mockbank images, push to ECR tagged with the commit SHA
  -> SSM Run Command on the instance: docker compose pull && up -d
  -> smoke test https://<host>/api/health reports the new SHA
```

Pull requests run CI and build the images without pushing.

On API startup, `server/api/schema.sql` runs idempotent `CREATE TABLE IF NOT EXISTS` statements, including the `conversacion` table. Therefore, pushing this change to `main` deploys the API and creates the table in the existing Postgres database; no separate AWS resource or manual migration command is needed. The existing Postgres data volume is retained across deploys.

### One-time setup

You need the AWS CLI logged in with an admin user (`aws configure` or `aws sso login`) and bash (Git Bash on Windows).

1. Run the bootstrap from the repo root:

   ```bash
   ALERT_EMAIL=you@example.com ANTHROPIC_API_KEY=sk-ant-... ./infra/bootstrap.sh
   ```

   Both variables are optional. `ALERT_EMAIL` creates a 25 USD/month budget alert. `AWS_REGION` defaults to `us-east-1`. The script is safe to re-run.

2. In GitHub, go to Settings > Secrets and variables > Actions and add the values the script prints: `AWS_ROLE_ARN` as a **secret**, and `AWS_REGION`, `EC2_INSTANCE_ID`, `APP_HOST` as **variables**.

3. Push to `main`, or run the Deploy workflow by hand from the Actions tab.

### Save money

```bash
./infra/power.sh stop     # stopped instance: about 5 USD/month for disk and IP
./infra/power.sh start
./infra/power.sh status
```

### Secrets

Secrets live in SSM Parameter Store under `/build101/`. On every deploy, `infra/remote-deploy.sh` writes them to `.env` on the server. To change one:

```bash
MSYS_NO_PATHCONV=1 aws ssm put-parameter --name /build101/ANTHROPIC_API_KEY --type SecureString --value sk-ant-... --overwrite
```

Then redeploy.

### Debug the server

No SSH port is open. Get a shell through SSM (needs the Session Manager plugin for the AWS CLI):

```bash
aws ssm start-session --target <EC2_INSTANCE_ID>
cd /opt/build101/infra && sudo docker compose logs -f api
```
