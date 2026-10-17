# Connect Claude Code to the team server

Claude Code calls the team server on every hook. The server records what your session is doing and answers with what is relevant for it right now:

- **Conflict warning.** Before you edit a file that another live session edited in the last 15 minutes, the edit is stopped once with who, when and what they were doing. Retry and it goes through.
- **Team discoveries.** Notes published for a host, file or repo you are touching reach your agent at its next hook, once.
- **Subagent brief.** When your session starts a subagent, the subagent gets the parent's task, edited files, recent failures and team notes.

If the server is down or slow, hooks fail open after 5 seconds and the agent keeps working.

## What leaves your machine

Repo-relative file paths, tool names, commands, error messages and prompts, each truncated to 500 characters and scrubbed for common secrets (bearer tokens, API keys, passwords, credentials in URLs). File contents and tool output are never stored.

## Setup

1. Get your API key (`b101_...`) from whoever runs the server.

2. Set it as an environment variable named `BUILD101_KEY`, then restart your terminal and editor:

   ```powershell
   # Windows (PowerShell)
   [Environment]::SetEnvironmentVariable("BUILD101_KEY", "b101_...", "User")
   ```

   ```bash
   # macOS / Linux: add to ~/.zshrc or ~/.bashrc
   export BUILD101_KEY=b101_...
   ```

3. Add the hooks from [settings.example.json](settings.example.json) to the project you work on, in `.claude/settings.local.json` (not committed). If that file already has a `hooks` section, merge the entries. To enable it for every project, use `~/.claude/settings.json` instead.

4. Start Claude Code in that project. Your session appears on the dashboard within a few seconds.

Sessions are grouped by repo using the name of the folder Claude Code starts in, so teammates should open the same repo folder name. To set the name explicitly, add `"X-Build101-Repo": "acme/payments"` to the `headers` of each HTTP hook.

## Share a discovery

Publish a note for the sessions it is relevant to. Topics are `host:<name>`, `file:<repo-relative path>` or `repo:<folder name>`; no topics means the whole team.

```bash
curl -X POST https://54-89-124-87.sslip.io/api/messages \
  -H "Authorization: Bearer $BUILD101_KEY" -H "Content-Type: application/json" \
  -d '{"kind": "discovery", "body": "Sandbox returns HTTP 200 with the error in the body.", "topics": ["host:sandbox.bankx.com"]}'
```

Kinds: `discovery` (lasts 7 days), `alert` (1 hour, delivered first), `note` (1 day). `GET /api/messages` lists them with how many sessions received each one.

## Check it works

```bash
curl -s -X POST https://54-89-124-87.sslip.io/api/hooks/claude-code \
  -H "Authorization: Bearer $BUILD101_KEY" -H "Content-Type: application/json" \
  -d '{"hook_event_name": "Stop", "session_id": "setup-check", "cwd": "/tmp/check"}'
```

It prints `{}`. A 401 means the key is missing or wrong.
