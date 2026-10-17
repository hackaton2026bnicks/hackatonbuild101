# Connect Claude Code to the team server

Claude Code calls the team server on every hook. The server records what your session is doing and answers with what is relevant for it right now:

- **Conflict warning.** Before you edit a file that another live session edited in the last 15 minutes, the edit is stopped once with who, when and what they were doing. Retry and it goes through.
- **Team discoveries.** Notes published for a host, file or repo you are touching reach your agent at its next hook, once.
- **Subagent brief.** When your session starts a subagent, the subagent gets the parent's task, edited files, recent failures and team notes.

If the server is down or slow, the hook gives up after 4 seconds and the agent keeps working.

## What leaves your machine

Every hook goes through [forward.py](forward.py), which runs on your machine and sends only:

- event name, session id, working directory, tool name, subagent id and type
- file paths (the server turns them into repo-relative paths)
- commands, URLs, prompts and error messages, scrubbed and cut to 2,000 characters

Scrubbing happens before anything is sent. It removes known secret formats (bearer tokens, JWTs, API keys, AWS and GitHub keys, `password=`-style values, credentials in URLs), random-looking tokens, and the literal value of any environment variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSW`, `CREDENTIAL` or `AUTH`. The server scrubs again before storing.

File contents, edit strings, tool output and the transcript are never sent.

## Setup inside this repo

The hooks are already configured in [.claude/settings.json](../../.claude/settings.json). Only your key is personal:

1. Get your API key (`b101_...`) from whoever runs the server.
2. Set it as `BUILD101_KEY` (see step 3 below for the commands), then restart your terminal and editor.
3. Open Claude Code in this repo. Your session shows up on the dashboard.

Without `BUILD101_KEY` the hooks do nothing and send nothing. Use the ignored `scratch/` folder to try conflicts with a teammate without touching real files.

## Setup in another project

1. Clone this repo anywhere and note its path.

2. Get your API key (`b101_...`) from whoever runs the server.

3. Set two environment variables, then restart your terminal and editor:

   ```powershell
   # Windows (PowerShell)
   [Environment]::SetEnvironmentVariable("BUILD101_KEY", "b101_...", "User")
   [Environment]::SetEnvironmentVariable("BUILD101_HOME", "C:/Users/you/code/hackatonbuild101", "User")
   ```

   ```bash
   # macOS / Linux: add to ~/.zshrc or ~/.bashrc
   export BUILD101_KEY=b101_...
   export BUILD101_HOME=~/code/hackatonbuild101
   ```

   Use forward slashes in `BUILD101_HOME` on Windows.

4. Add the hooks from [settings.example.json](settings.example.json) to the project you work on, in `.claude/settings.local.json` (not committed). If that file already has a `hooks` section, merge the entries. To enable it for every project, use `~/.claude/settings.json` instead. On macOS and Linux, change `python` to `python3` in each command.

5. Start Claude Code in that project. Your session appears on the dashboard within a few seconds.

Sessions are grouped by repo using the name of the folder Claude Code starts in, so teammates should open the same repo folder name. To set it explicitly, also set `BUILD101_REPO=acme/payments`.

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
echo '{"hook_event_name": "Stop", "session_id": "setup-check", "cwd": "/tmp/check"}' \
  | python "$BUILD101_HOME/agent/claude-code/forward.py"; echo "exit $?"
```

It prints `exit 0` and the session `setup-check` shows up on the dashboard. If it does not show up, check that `BUILD101_KEY` is set in that terminal.
