# build101: situational memory for coding agents

Lessons that one coding agent learned the hard way are pushed to the next agent right before it makes the same mistake, even if that agent belongs to a teammate or comes from another vendor. Full design in [docs/SPEC.md](docs/SPEC.md).

## Layout

```
server/     team API: event ingestion, lessons, search, live feed (FastAPI)
worker/     background jobs: distiller and criteria miner
scenario/   mock bank with undocumented traps for the demo
ui/         team dashboard (static, served by Caddy)
infra/      docker compose, Caddy, AWS bootstrap and deploy scripts
docs/       spec
```

Coming next: `agent/` (local hook receiver, scrubber, canonicalizer, trigger index) and `adapters/`.

## Run locally

```bash
python -m venv .venv && source .venv/Scripts/activate   # .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt -r server/requirements.txt -r scenario/requirements.txt
ruff check . && pytest
```

Full stack with Docker (Postgres, Caddy and all services, built from source):

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml --env-file infra/.env.example up --build
```

Then open http://localhost. The API is under `/api`, the mock bank under `/bank`.

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

### One-time setup

You need the AWS CLI logged in with an admin user (`aws configure` or `aws sso login`) and bash (Git Bash on Windows).

1. Run the bootstrap from the repo root:

   ```bash
   ALERT_EMAIL=you@example.com ANTHROPIC_API_KEY=sk-ant-... ./infra/bootstrap.sh
   ```

   Both variables are optional. `ALERT_EMAIL` creates a 25 USD/month budget alert. `AWS_REGION` defaults to `us-east-1`. The script is safe to re-run.

2. In GitHub, go to Settings > Secrets and variables > Actions > Variables and add the four values the script prints: `AWS_REGION`, `AWS_ROLE_ARN`, `EC2_INSTANCE_ID`, `APP_HOST`.

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
