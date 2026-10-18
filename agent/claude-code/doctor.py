"""Checks that this machine is connected to the build101 team, and says how to fix what is not.

    python agent/claude-code/doctor.py        (python3 on macOS)

It checks the key, the server, the hook settings, that accents survive the trip, the MCP server, the
checkout (on main and up to date, so the hooks run the code that is deployed) and whether the server has
seen your sessions lately. It sends nothing that creates a session or a message. Standard library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from mcp_server import api_base  # noqa: E402

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
SEEN_RECENTLY_MINUTES = 60


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, what: str) -> None:
        print(f"  OK    {what}")

    def bad(self, what: str, fix: str) -> None:
        self.failed += 1
        print(f"  FAIL  {what}\n        -> {fix}")

    def warn(self, what: str, fix: str) -> None:
        print(f"  WARN  {what}\n        -> {fix}")


def get(path: str, environ: dict) -> dict:
    request = urllib.request.Request(
        api_base(environ) + path, headers={"Authorization": f"Bearer {environ.get('BUILD101_KEY', '')}"}
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read() or b"{}")


def check_python(report: Report) -> None:
    if sys.version_info >= (3, 9):  # noqa: UP036 (this script must run on the 3.9 macOS ships)
        report.ok(f"Python {sys.version.split()[0]}")
    else:
        report.bad(f"Python {sys.version.split()[0]} is too old", "Install Python 3.9 or newer.")


def check_server(report: Report, environ: dict) -> str | None:
    if not environ.get("BUILD101_KEY"):
        report.bad(
            "BUILD101_KEY is not set in this shell",
            "Ask Nico for a key and export it (~/.zshrc on macOS, setx BUILD101_KEY ... on Windows). "
            "On macOS the desktop app does not read ~/.zshrc: also put it in .claude/settings.local.json "
            '{"env": {"BUILD101_KEY": "..."}}.',
        )
        return None
    try:
        me = get("/me", environ)
    except urllib.error.HTTPError as error:
        fix = "The key was revoked or mistyped; ask for a new one." if error.code == 401 else "Retry later."
        report.bad(f"The server answered {error.code} to your key", fix)
        return None
    except (urllib.error.URLError, OSError) as error:
        report.bad(
            f"The server is unreachable ({error})", "Check your connection; ask Nico if the server is down."
        )
        return None
    report.ok(f"Server reachable, your key is {me.get('user')} on team {me.get('team')}")
    return me.get("user")


def check_hooks(report: Report) -> None:
    try:
        hooks = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError) as error:
        report.bad(f"Cannot read .claude/settings.json ({error})", "git pull origin main.")
        return
    commands = {
        e: [h.get("command", "") for m in hooks.get(e, []) for h in m.get("hooks", [])] for e in HOOK_EVENTS
    }
    missing = [e for e in HOOK_EVENTS if not any("forward.py" in c for c in commands[e])]
    if missing:
        report.bad(
            f"Hooks missing for {', '.join(missing)}", "git pull origin main, then /hooks in Claude Code."
        )
    else:
        report.ok("Hooks configured for every event build101 uses")
    local = ROOT / ".claude" / "settings.local.json"
    if local.exists():
        try:
            if json.loads(local.read_text(encoding="utf-8")).get("disableAllHooks"):
                report.bad("disableAllHooks is on in .claude/settings.local.json", "Remove it.")
        except ValueError:
            report.bad(".claude/settings.local.json is not valid JSON", "Fix or delete it.")


def check_encoding(report: Report) -> None:
    """Pipe UTF-8 into the forwarder the way Claude Code does and read it back."""
    text = "análisis ñandú ¿listo?"
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); from forward import read_stdin_json; "
        "sys.stdout.buffer.write(read_stdin_json()['prompt'].encode('utf-8'))"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", script, str(HERE)],
            input=json.dumps({"prompt": text}, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=20,
        )
        read = out.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        read = repr(error)
    if read == text:
        report.ok("Accents survive the trip from Claude Code to the forwarder")
    else:
        report.bad(f"The forwarder read {read!r}", "git pull origin main (the UTF-8 fix is PR #22).")


def check_mcp(report: Report, environ: dict) -> None:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        out = subprocess.run(
            [sys.executable, str(HERE / "mcp_server.py")],
            input="".join(json.dumps(m) + "\n" for m in messages).encode("utf-8"),
            capture_output=True,
            timeout=20,
            env=environ,
        )
        replies = [json.loads(line) for line in out.stdout.decode("utf-8").splitlines() if line.strip()]
        tools = [t["name"] for t in replies[1]["result"]["tools"]]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError) as error:
        report.bad(f"The MCP server does not start ({error})", "Run it by hand to see the error.")
        return
    report.ok(f"MCP server starts with {len(tools)} tools ({', '.join(tools)})")
    if not (ROOT / ".mcp.json").exists():
        report.bad(".mcp.json is missing at the repo root", "git pull origin main.")
    if sys.platform == "darwin" and not environ.get("BUILD101_PYTHON"):
        report.warn("BUILD101_PYTHON is not set", "macOS has no `python`: export BUILD101_PYTHON=python3.")


def git(*args: str, cwd: Path = ROOT) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def check_checkout(report: Report) -> None:
    """Hooks run the main folder's scripts, even from a worktree: off main or behind, they run old code."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    main_folder = Path(common).parent if common else ROOT
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=main_folder)
    if not branch:
        report.warn("Not a git checkout", "Clone the repo to get updates.")
        return
    git("fetch", "-q", "origin", "main", cwd=main_folder)
    behind = git("rev-list", "--count", "HEAD..origin/main", cwd=main_folder) or "0"
    if branch != "main":
        report.warn(
            f"{main_folder} is on branch {branch}, so the hooks run that branch's scripts",
            "Keep the main folder on main and do branch work in a worktree (git worktree add).",
        )
    if behind != "0":
        report.bad(
            f"{behind} commit(s) behind origin/main", "git pull origin main, then restart Claude Code."
        )
    elif branch == "main":
        report.ok("On main and up to date")


def check_seen(report: Report, environ: dict, user: str) -> None:
    try:
        sessions = get(f"/sessions?active_minutes={24 * 60}", environ).get("sessions", [])
    except (urllib.error.URLError, OSError, ValueError):
        return
    mine = [s for s in sessions if s.get("user") == user]
    if not mine:
        report.bad(
            "The server has not seen any session of yours in the last 24 h",
            "Open Claude Code at the repo root, approve the hooks (/hooks), send a prompt, run this again.",
        )
        return
    last = max(datetime.fromisoformat(s["last_event_at"].replace("Z", "+00:00")) for s in mine)
    minutes = int((datetime.now(timezone.utc) - last).total_seconds() // 60)  # noqa: UP017 (3.9)
    if minutes > SEEN_RECENTLY_MINUTES:
        report.warn(
            f"Your last hook reached the server {minutes} min ago",
            "If you are working now, hooks are not arriving.",
        )
    else:
        report.ok(f"Your hooks are arriving: last one {minutes} min ago, {len(mine)} session(s) today")


def check_vault(report: Report, environ: dict) -> None:
    try:
        vault = get("/vault?limit=1", environ).get("vault", {})
    except (urllib.error.URLError, OSError, ValueError):
        return
    if not vault.get("configured"):
        report.warn("The server has no team vault connected", "Ask Nico: the server has no VAULT_GIT.")
    elif vault.get("error") and not vault.get("notes"):
        report.bad("The server cannot read the team vault", vault["error"])
    else:
        report.ok(f"Team vault: {vault.get('notes', 0)} notes reach your sessions when they apply")


def main() -> int:
    environ = dict(os.environ)
    report = Report()
    print("build101 doctor\n")
    check_python(report)
    user = check_server(report, environ)
    check_hooks(report)
    check_encoding(report)
    check_mcp(report, environ)
    check_checkout(report)
    if user:
        check_seen(report, environ, user)
        check_vault(report, environ)
    print("\nAll good." if not report.failed else f"\n{report.failed} problem(s) to fix.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
