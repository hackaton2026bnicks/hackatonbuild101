"""Claude Code hook forwarder: cleans the hook payload on this machine, then sends it to the team server.

Only the fields the server uses are sent, with secrets scrubbed here. File contents, edit strings and
tool output never leave the machine. On any failure it prints nothing and exits 0, so the agent keeps
working. Standard library only.

Hook command:  python /path/to/hackatonbuild101/agent/claude-code/forward.py
Environment:   BUILD101_KEY (required), BUILD101_URL (optional), BUILD101_REPO (optional repo name)
"""

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the server's scrubber so both sides apply the same rules
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from api.scrub import scrub  # noqa: E402

DEFAULT_URL = "https://54-89-124-87.sslip.io/api/hooks/claude-code"
TIMEOUT_SECONDS = 4
# The API restarts on every deploy; one quick retry keeps SessionEnd and Stop from being lost
RETRY_AFTER_SECONDS = 1
RESTARTING_STATUSES = {502, 503, 504}
# socket.timeout only became an alias of TimeoutError in Python 3.10; macOS ships 3.9
TIMEOUTS = (socket.timeout, TimeoutError)
TEXT_LIMIT = 2000

PLAIN_FIELDS = (
    "hook_event_name",
    "session_id",
    "cwd",
    "tool_name",
    "agent_id",
    "agent_type",
    "source",
    "stop_hook_active",
    "trigger",
)

SCRUBBED_FIELDS = ("prompt", "error")
PATH_INPUTS = ("file_path", "notebook_path")
SCRUBBED_INPUTS = ("command", "url")
# Which skill or subagent the agent called: the name only, never its args or prompt
NAME_INPUTS = ("skill", "subagent_type")
SENSITIVE_ENV_NAME = re.compile(r"(?i)(key|token|secret|passw|credential|auth)")


def env_secrets(environ: dict) -> list[str]:
    """Values of sensitive-looking environment variables, longest first, to blank out verbatim."""
    values = {
        value
        for name, value in environ.items()
        if SENSITIVE_ENV_NAME.search(name) and name != "BUILD101_URL" and len(value) >= 8
    }
    return sorted(values, key=len, reverse=True)


def clean(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[REDACTED:env]")
    return scrub(text, TEXT_LIMIT)


def workspace(cwd: str, project: str) -> str:
    """The checkout the session's shell works in: its Claude Code worktree, or else the project folder.

    Two sessions in the same checkout share a branch: a `git checkout` in one changes the other's files.
    """
    path, root = cwd.replace("\\", "/"), project.replace("\\", "/").rstrip("/")
    if not root:
        return path
    prefix = root + "/.claude/worktrees/"
    if path.lower().startswith(prefix.lower()):
        return prefix + path[len(prefix) :].split("/")[0]
    return root


def sanitize(raw: dict, environ: dict) -> dict:
    secrets = env_secrets(environ)
    out = {field: str(raw[field])[:TEXT_LIMIT] for field in PLAIN_FIELDS if raw.get(field) is not None}
    # The hook's cwd follows the agent's `cd`; the project root keeps the repo name and paths stable
    if environ.get("CLAUDE_PROJECT_DIR"):
        out["cwd"] = environ["CLAUDE_PROJECT_DIR"][:TEXT_LIMIT]
    if isinstance(raw.get("cwd"), str):
        out["workspace"] = workspace(raw["cwd"], environ.get("CLAUDE_PROJECT_DIR", ""))[:TEXT_LIMIT]
    for field in SCRUBBED_FIELDS:
        if isinstance(raw.get(field), str):
            out[field] = clean(raw[field], secrets)

    tool_input = raw.get("tool_input")
    if isinstance(tool_input, dict):
        kept = {f: tool_input[f][:TEXT_LIMIT] for f in PATH_INPUTS if isinstance(tool_input.get(f), str)}
        kept |= {f: tool_input[f][:200] for f in NAME_INPUTS if isinstance(tool_input.get(f), str)}
        kept |= {
            f: clean(tool_input[f], secrets) for f in SCRUBBED_INPUTS if isinstance(tool_input.get(f), str)
        }
        if kept:
            out["tool_input"] = kept
    return out


def post(request, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """Send once, and once more if the server looks like it is restarting. Timeouts are not retried,
    so a server that is really down never costs the agent more than one timeout."""
    for attempt in range(2):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            if error.code not in RESTARTING_STATUSES or attempt:
                return None
        except TIMEOUTS:
            return None
        except (urllib.error.URLError, ConnectionError) as error:
            if attempt or isinstance(getattr(error, "reason", None), TIMEOUTS):
                return None
        sleep(RETRY_AFTER_SECONDS)
    return None


def read_stdin_json(stdin=None):
    """Claude Code writes UTF-8; on Windows sys.stdin decodes cp1252 and turns á into Ã¡."""
    return json.loads((stdin or sys.stdin).buffer.read().decode("utf-8"))


def main() -> None:
    try:
        raw = read_stdin_json()
        key = os.environ.get("BUILD101_KEY")
        if not key or not isinstance(raw, dict):
            return
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if os.environ.get("BUILD101_REPO"):
            headers["X-Build101-Repo"] = os.environ["BUILD101_REPO"]
        request = urllib.request.Request(
            os.environ.get("BUILD101_URL", DEFAULT_URL),
            data=json.dumps(sanitize(raw, dict(os.environ))).encode(),
            headers=headers,
            method="POST",
        )
        answer = post(request)
        if isinstance(answer, dict) and answer:
            print(json.dumps(answer))
    except Exception:
        # Never break the agent because the team server is unreachable or misconfigured
        pass


if __name__ == "__main__":
    main()
