"""Claude Code hook adapter: turns a raw hook payload into what the server needs.

Only metadata is kept: repo-relative paths, scrubbed commands, errors and prompts.
File contents and tool output never leave this function.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath

from api.scrub import scrub

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "NotebookRead"}

EVENT_TYPES = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "intent",
    "PreToolUse": "pre_action",
    "PostToolUse": "post_action",
    "PostToolUseFailure": "post_action",
    "PreCompact": "compact",
    "PostCompact": "compact",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
    "SessionEnd": "session_end",
}

# Events whose response may carry hookSpecificOutput.additionalContext
CONTEXT_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
}

URL_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
# From "<<EOF" (or <<'EOF', <<-EOF) to the line that closes it
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?\n.*?^\s*\2\s*$", re.DOTALL | re.MULTILINE)
MESSAGE_ARG = re.compile(r"""(?:-m|--message|--body|--title)(?:\s+|=)("(?:[^"\\]|\\.)*"|'[^']*')""")
TEXT_LIMIT = 500
WORKTREES = (".claude", "worktrees")
SUMMARY_LIMIT = 160


@dataclass
class HookCall:
    event_name: str
    event_type: str
    session_id: str
    repo: str
    agent_id: str | None = None
    agent_type: str | None = None
    tool: str | None = None
    file: str | None = None
    edits_file: bool = False
    hosts: list[str] = field(default_factory=list)
    stop_hook_active: bool = False
    summary: str = ""
    payload: dict = field(default_factory=dict)


def _pure_path(path: str):
    return PureWindowsPath(path) if ("\\" in path or re.match(r"^[A-Za-z]:", path)) else PurePosixPath(path)


def repo_relative(file_path: str, cwd: str) -> str | None:
    """Path relative to the session's working directory, so two machines agree on it."""
    if not file_path or not cwd:
        return None
    try:
        parts = _pure_path(file_path).relative_to(_pure_path(cwd)).parts
    except ValueError:
        return None
    # A Claude Code worktree is a copy of the repo: its files are the repo's files
    if parts[:2] == WORKTREES and len(parts) > 3:
        parts = parts[3:]
    return PurePosixPath(*parts).as_posix()


def repo_name(cwd: str, header: str | None) -> str:
    if header:
        return header.strip()[:200]
    return _pure_path(cwd).name if cwd else "unknown"


def hosts_in(text: str) -> list[str]:
    return sorted({host.lower() for host in URL_HOST.findall(text)})


def command_hosts(command: str) -> list[str]:
    """Hosts a command calls. A URL inside a heredoc or a commit or PR message is text, not a call."""
    return hosts_in(MESSAGE_ARG.sub(" ", HEREDOC.sub(" ", command)))


def parse(raw: dict, repo_header: str | None = None) -> HookCall | None:
    event_name = raw.get("hook_event_name")
    session_id = raw.get("session_id")
    if event_name not in EVENT_TYPES or not session_id:
        return None

    cwd = raw.get("cwd") or ""
    call = HookCall(
        event_name=event_name,
        event_type=EVENT_TYPES[event_name],
        session_id=str(session_id)[:128],
        repo=repo_name(cwd, repo_header),
        agent_id=raw.get("agent_id"),
        agent_type=raw.get("agent_type"),
        tool=raw.get("tool_name"),
        stop_hook_active=str(raw.get("stop_hook_active")).lower() == "true",
    )
    tool_input = raw.get("tool_input") if isinstance(raw.get("tool_input"), dict) else {}
    payload: dict = {"hook": event_name}

    if call.tool:
        payload["tool"] = call.tool
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if isinstance(file_path, str):
            call.file = repo_relative(file_path, cwd)
            call.edits_file = call.tool in EDIT_TOOLS and call.file is not None
            payload["file"] = call.file or PurePosixPath(file_path.replace("\\", "/")).name
        command = tool_input.get("command")
        if isinstance(command, str):
            payload["command"] = scrub(command, TEXT_LIMIT)
            call.hosts = command_hosts(command)
        url = tool_input.get("url")
        if isinstance(url, str):
            call.hosts = sorted(set(call.hosts) | set(hosts_in(url)))
        if call.hosts:
            payload["hosts"] = call.hosts
        # Skill and subagent names count capital use per session (DIK v2, layer A)
        for key in ("skill", "subagent_type"):
            if isinstance(tool_input.get(key), str) and tool_input[key]:
                payload[key] = tool_input[key][:128]

    if event_name == "PostToolUseFailure" and isinstance(raw.get("error"), str):
        payload["error"] = scrub(raw["error"], TEXT_LIMIT)
    if event_name == "UserPromptSubmit" and isinstance(raw.get("prompt"), str):
        payload["prompt"] = scrub(raw["prompt"], TEXT_LIMIT)
    if event_name == "SessionStart" and raw.get("source"):
        payload["source"] = str(raw["source"])[:32]
    if event_name == "PreCompact" and raw.get("trigger"):
        payload["trigger"] = str(raw["trigger"])[:32]
    for key in ("agent_id", "agent_type"):
        if raw.get(key):
            payload[key] = str(raw[key])[:128]

    call.summary = summarize(call, payload)
    payload["canonical"] = call.summary
    call.payload = payload
    return call


def summarize(call: HookCall, payload: dict) -> str:
    """One readable line for the feed and for teammates' context."""
    if "error" in payload:
        first_line = payload["error"].splitlines()[0] if payload["error"] else ""
        return scrub(f"error({call.tool}: {first_line})", SUMMARY_LIMIT)
    if call.event_name == "UserPromptSubmit":
        return payload.get("prompt", "")[:SUMMARY_LIMIT]
    if call.tool in EDIT_TOOLS:
        return f"edit({payload.get('file', '?')})"
    if call.tool in READ_TOOLS:
        return f"read({payload.get('file', '?')})"
    if call.tool == "Bash":
        return f"bash({payload.get('command', '')})"[:SUMMARY_LIMIT]
    if call.tool == "WebFetch" and call.hosts:
        return f"fetch({call.hosts[0]})"
    if "skill" in payload:
        return f"skill({payload['skill']})"
    if "subagent_type" in payload:
        return f"agent({payload['subagent_type']})"
    if call.tool:
        return call.tool.lower()
    if call.event_name == "SubagentStart":
        return f"subagent({call.agent_type or 'agent'})"
    return call.event_type
