# Connect Claude Code to the team server

Claude Code calls the team server on every hook. The server records what your session is doing and answers with what is relevant for it right now:

- **Overlap notice.** When your agent starts editing a file another live session edited in the last 15 minutes, nothing is blocked: your agent learns who is there and what they are doing, and their agent learns at its next hook that you joined. Each pair is told once per file.
- **Team discoveries.** Notes published for a host, file or repo you are touching reach your agent at its next hook, once.
- **Subagent brief.** When your session starts a subagent, the subagent gets the parent's task, edited files, recent failures and team notes.
- **Who else is here.** When a session starts, it learns which teammates are working in the same repo, on what task and which files, and catches up on the team's latest 10 messages.
- **Questions and answers.** Questions from teammates' agents reach you at your next hook; an answer reaches only the session that asked.

If the server is down or slow, the hook gives up after 4 seconds and the agent keeps working.

## Team tools for the agent

[mcp_server.py](mcp_server.py) is a local MCP server, registered in [.mcp.json](../../.mcp.json), so the agent can talk to the team on its own:

| Tool | What it does |
| --- | --- |
| `share_discovery` | Publishes something it learned to sessions on the same repo, files or hosts (`alert` for urgent, 1 hour). |
| `ask_team` | Asks a question; the agent keeps working and the answer arrives in a later turn. |
| `answer_team` | Answers a question it received as `[build101 · question #N]`. |
| `team_status` | Lists live sessions in the repo, their task and edited files, and unanswered questions. |

Restart Claude Code in this repo and approve the `build101` server when asked. It uses the same `BUILD101_KEY`, scrubs what the agent writes with the forwarder's rules, and sends no file contents. Where `python` is not on PATH (macOS), also set `BUILD101_PYTHON=python3`.

## What leaves your machine

Every hook goes through [forward.py](forward.py), which runs on your machine and sends only:

- event name, session id, working directory, tool name, subagent id and type
- the name of the skill or subagent type the agent calls (never its arguments or prompt), and whether a compaction was manual or automatic
- file paths (the server turns them into repo-relative paths)
- commands, URLs, prompts and error messages, scrubbed and cut to 2,000 characters

Scrubbing happens before anything is sent. It removes known secret formats (bearer tokens, JWTs, API keys, AWS and GitHub keys, `password=`-style values, credentials in URLs), random-looking tokens, and the literal value of any environment variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSW`, `CREDENTIAL` or `AUTH`. The server scrubs again before storing.

File contents, edit strings, tool output and the transcript are never sent.

## Setup inside this repo

The hooks are already configured in [.claude/settings.json](../../.claude/settings.json). Only your key is personal:

1. Get your API key (`b101_...`) from whoever runs the server.
2. Set it as `BUILD101_KEY` (see step 3 below for the commands), then restart your terminal and editor.
3. Open Claude Code in this repo. Your session shows up on the dashboard.

Without `BUILD101_KEY` the hooks do nothing and send nothing. Use the ignored `scratch/` folder to try overlaps with a teammate without touching real files.

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

## Measure your own sessions (optional)

[session_end.py](session_end.py) is a separate `SessionEnd` hook that sends nothing to the server. When a session closes it starts a background process and returns at once; that process appends the session's facts and its work card (a small model through `claude -p`) to `~/.claude/session-insights/`, and `tools/weekly_report.py` turns them into a weekly summary. Install and cost are in [DIK-INSIGHTS.md](../../docs/DIK-INSIGHTS.md#instalar-la-parte-hacia-adelante).

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
