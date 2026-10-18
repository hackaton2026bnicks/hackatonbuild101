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
import sys
import urllib.request
from pathlib import Path

# Reuse the server's scrubber so both sides apply the same rules
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
from api.scrub import scrub  # noqa: E402

DEFAULT_URL = "https://54-89-124-87.sslip.io/api/hooks/claude-code"
TIMEOUT_SECONDS = 4
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
)
SCRUBBED_FIELDS = ("prompt", "error")
PATH_INPUTS = ("file_path", "notebook_path")
SCRUBBED_INPUTS = ("command", "url")
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


def sanitize(raw: dict, environ: dict) -> dict:
    secrets = env_secrets(environ)
    out = {field: str(raw[field])[:TEXT_LIMIT] for field in PLAIN_FIELDS if raw.get(field) is not None}
    # The hook's cwd follows the agent's `cd`; the project root keeps the repo name and paths stable
    if environ.get("CLAUDE_PROJECT_DIR"):
        out["cwd"] = environ["CLAUDE_PROJECT_DIR"][:TEXT_LIMIT]
    for field in SCRUBBED_FIELDS:
        if isinstance(raw.get(field), str):
            out[field] = clean(raw[field], secrets)

    tool_input = raw.get("tool_input")
    if isinstance(tool_input, dict):
        kept = {f: tool_input[f][:TEXT_LIMIT] for f in PATH_INPUTS if isinstance(tool_input.get(f), str)}
        kept |= {
            f: clean(tool_input[f], secrets) for f in SCRUBBED_INPUTS if isinstance(tool_input.get(f), str)
        }
        if kept:
            out["tool_input"] = kept
    return out


def main() -> None:
    try:
        raw = json.load(sys.stdin)
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
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read() or b"{}")
        if isinstance(answer, dict) and answer:
            print(json.dumps(answer))
    except Exception:
        # Never break the agent because the team server is unreachable or misconfigured
        pass


if __name__ == "__main__":
    main()
