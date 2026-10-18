"""Read local Claude Code transcripts and write, per session, deterministic usage facts and a condensed,
scrubbed timeline for the extractor. Raw transcripts never leave this machine.

Usage: python3 tools/backfill/sessions.py [--projects ~/.claude/projects] [--out data/backfill]

Writes:
  <out>/facts.jsonl            one line per session: who, where, how long, which tools, errors, compactions
  <out>/condensed/<id>.txt     prompts, assistant text, canonical tool calls and errors, cut to fit a prompt
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import re
import shlex
import sys
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))
from api.scrub import scrub  # noqa: E402

# Gaps longer than this between events count as idle time, not work
IDLE_MINUTES = 30
CONDENSED_LIMIT = 40_000
PROMPT_LIMIT = 800
TEXT_LIMIT = 350
FINAL_TEXT_LIMIT = 1500
ERROR_LIMIT = 220

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND_NAME = re.compile(r"<command-name>\s*/?([\w:.-]+)\s*</command-name>")
CORRECTION = re.compile(
    r"^(no[\s,.!]|nop\b|eso no|no es (as[ií]|eso)|mal\b|est[aá] mal|te equivocaste|no hagas|"
    r"pará|frená|nunca\b|siempre\b|ojo\b|te dije|ya te dije|otra vez|wait\b|stop\b)",
    re.I,
)
DENIED = re.compile(r"doesn't want to proceed|was rejected|permission to use .* (was|has been) denied", re.I)
URL_HOST = re.compile(r"https?://[^\s'\"<>]+")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Read-only shell noise: kept in counts, left out of action sequences
QUIET = {
    "read", "grep", "glob", "ls", "cat", "head", "tail", "sed", "wc", "echo", "find", "cd", "awk", "sort",
    "toolsearch", "todowrite", "mkdir", "pwd", "for", "true", "sleep", "jq", "diff", "test", "which",
}


def host_of(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def repo_of(cwd: str) -> tuple[str, str]:
    """Return (repo, subdir) for a working directory under ~/repos or ~/agents."""
    path = re.sub(r"/\.(claude/)?worktrees/[^/]+", "", cwd or "")
    parts = path.strip("/").split("/")
    for anchor in ("repos", "agents"):
        if anchor in parts:
            base = "/" + "/".join(parts[: parts.index(anchor) + 1])
            rest = parts[parts.index(anchor) + 1 :]
            if not rest:
                continue
            # The deepest git root wins; a folder grouping repos (clientes-skum/medical) keeps two levels
            for n in range(len(rest), 0, -1):
                if os.path.exists(os.path.join(base, *rest[:n], ".git")):
                    return "/".join(rest[:n]), "/".join(rest[n:])
            if len(rest) > 1:
                return "/".join(rest[:2]), "/".join(rest[2:])
            return rest[0], ""
    return (parts[-1] if parts and parts[-1] else "?"), ""


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = (b for b in content if isinstance(b, dict) and b.get("type") == "text")
        return "\n".join(b.get("text", "") for b in blocks)
    return ""


def result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content if isinstance(content, str) else ""


def bash_canon(command: str) -> tuple[str, str]:
    """Reduce a shell command to a canonical token and a short detail for the timeline."""
    cmd = command.strip()
    cmd = re.sub(r"^(cd\s+[^&;|]+(&&|;)\s*)+", "", cmd)
    cmd = re.sub(r"^([A-Z_][A-Z0-9_]*=\S+\s+)+", "", cmd)
    first = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd, maxsplit=1)[0]
    try:
        words = shlex.split(first)
    except ValueError:
        words = first.split()
    if not words:
        return "bash:?", ""
    tool = os.path.basename(words[0])
    args = [w for w in words[1:] if not w.startswith("-")]
    detail = first[:120]
    if tool == "ssh":
        # Skip options and the values of options that take one (-o ConnectTimeout=10, -i key, -p 22)
        positional, skip = [], False
        for w in words[1:]:
            if skip:
                skip = False
            elif w.startswith("-"):
                skip = w in ("-o", "-i", "-p", "-l", "-F", "-J", "-L", "-R", "-D", "-W")
            else:
                positional.append(w)
        host = (positional[0] if positional else "?").split("@")[-1]
        remote = positional[1].split()[0] if len(positional) > 1 and positional[1].split() else ""
        return f"ssh:{host}" + (f">{os.path.basename(remote)}" if remote else ""), detail
    if tool in ("git", "gh", "docker", "npm", "npx", "pnpm", "yarn", "brew", "systemctl", "kubectl", "uv"):
        sub = args[0] if args else ""
        if tool == "docker" and sub == "compose" and len(args) > 1:
            sub = f"compose.{args[1]}"
        return f"{tool}:{sub}" if sub else tool, detail
    if tool.startswith("python"):
        script = next((os.path.basename(a) for a in args if a.endswith(".py")), "-c" if "-c" in words else "")
        return f"python:{script}" if script else "python", detail
    if tool == "curl":
        m = URL_HOST.search(first)
        return f"curl:{host_of(m.group())}" if m else "curl", detail
    if tool.endswith(".sh"):
        return f"script:{tool}", detail
    return tool, detail


def canon(name: str, inp: dict) -> tuple[str, str]:
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return bash_canon(inp.get("command", ""))
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        server, tool = (parts[1], parts[2]) if len(parts) == 3 else (parts[-1], "")
        detail = str(inp.get("sql") or inp.get("query") or inp.get("url") or "")[:120]
        return f"mcp:{server}.{tool}", detail
    if name == "Skill":
        return f"skill:{inp.get('skill', '?')}", str(inp.get("args", ""))[:80]
    if name in ("Agent", "Task"):
        return f"agent:{inp.get('subagent_type') or 'general-purpose'}", str(inp.get("description", ""))[:80]
    if name in EDIT_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        ext = os.path.splitext(path)[1].lstrip(".") or os.path.basename(path)
        return f"edit:{ext}", os.path.basename(path)
    return name.lower(), ""


def active_minutes(stamps: list[datetime]) -> float:
    stamps = sorted(stamps)
    total = 0.0
    for a, b in zip(stamps, stamps[1:], strict=False):
        gap = (b - a).total_seconds() / 60
        if gap <= IDLE_MINUTES:
            total += gap
    return round(total, 1)


def parse_ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class Session:
    def __init__(self, session_id: str, project_dir: str):
        self.id = session_id
        self.project_dir = project_dir
        self.cwd = ""
        self.title = ""
        self.ai_title = ""
        self.stamps: list[datetime] = []
        self.prompts: list[str] = []
        self.slash_commands: Counter = Counter()
        self.tools: Counter = Counter()
        self.canon: Counter = Counter()
        self.sequence: list[str] = []
        self.mcp: Counter = Counter()
        self.skills: Counter = Counter()
        self.subagents: Counter = Counter()
        self.subagent_tools: Counter = Counter()
        self.models: Counter = Counter()
        self.hosts: Counter = Counter()
        self.counts: Counter = Counter()
        self.message_ids: set[str] = set()
        self.timeline: list[tuple[str, str]] = []
        self.pending_tools: list[tuple[str, str]] = []

    def flush_tools(self):
        if not self.pending_tools:
            return
        grouped: dict[str, list[str]] = {}
        for token, detail in self.pending_tools:
            grouped.setdefault(token, []).append(detail)
        parts = []
        for token, details in list(grouped.items())[:8]:
            sample = next((d for d in details if d), "")
            times = f" ×{len(details)}" if len(details) > 1 else ""
            parts.append(f"{token}{times}" + (f" ({scrub(sample, 110)})" if sample else ""))
        if len(grouped) > 8:
            parts.append(f"+{len(grouped) - 8} más")
        self.timeline.append(("T", " · ".join(parts)))
        self.pending_tools = []

    def add(self, d: dict):
        ts = parse_ts(d.get("timestamp")) if d.get("timestamp") else None
        if ts:
            self.stamps.append(ts)
        if d.get("cwd") and not self.cwd:
            self.cwd = d["cwd"]
        kind = d.get("type")
        if kind == "custom-title":
            self.title = d.get("customTitle") or self.title
        elif kind == "ai-title":
            self.ai_title = d.get("aiTitle") or self.ai_title
        elif kind == "system":
            sub = d.get("subtype")
            if sub == "compact_boundary":
                trigger = (d.get("compactMetadata") or {}).get("trigger", "auto")
                self.counts[f"compactions_{trigger}"] += 1
                self.counts["compactions"] += 1
                self.flush_tools()
                self.timeline.append(("C", f"compactación ({trigger})"))
            elif sub == "api_error":
                self.counts["api_errors"] += 1
        elif kind == "assistant":
            self.add_assistant(d.get("message") or {})
        elif kind == "user":
            self.add_user(d)

    def add_assistant(self, message: dict):
        if message.get("model") and message.get("id") not in self.message_ids:
            self.message_ids.add(message.get("id"))
            self.models[message["model"]] += 1
            usage = message.get("usage") or {}
            self.counts["output_tokens"] += usage.get("output_tokens", 0) or 0
            self.counts["input_tokens"] += sum(
                usage.get(k, 0) or 0
                for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            )
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if block.get("type") == "text" and block.get("text", "").strip():
                self.flush_tools()
                self.timeline.append(("A", block["text"].strip()))
            elif block.get("type") == "tool_use":
                name, inp = block.get("name", "?"), block.get("input") or {}
                token, detail = canon(name, inp)
                self.tools[name] += 1
                self.canon[token] += 1
                if token.split(":")[0].split(">")[0] not in QUIET and token not in QUIET:
                    if not self.sequence or self.sequence[-1] != token:
                        self.sequence.append(token)
                if name.startswith("mcp__"):
                    self.mcp[name.split("__")[1]] += 1
                if name == "Skill":
                    self.skills[inp.get("skill", "?")] += 1
                if name in ("Agent", "Task"):
                    self.subagents[inp.get("subagent_type") or "general-purpose"] += 1
                if name == "Bash":
                    for m in URL_HOST.finditer(inp.get("command", "")):
                        if host_of(m.group()):
                            self.hosts[host_of(m.group())] += 1
                    if token.startswith("ssh:"):
                        self.hosts[token[4:].split(">")[0]] += 1
                self.pending_tools.append((token, detail))

    def add_user(self, d: dict):
        message = d.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    self.counts["tool_results"] += 1
                    text = result_text(block)
                    if DENIED.search(text):
                        self.counts["denials"] += 1
                    elif block.get("is_error"):
                        self.counts["tool_errors"] += 1
                        self.flush_tools()
                        self.timeline.append(("E", text.strip()))
        if d.get("isMeta") or d.get("isCompactSummary"):
            return
        origin = (d.get("origin") or {}).get("kind", "human")
        text = REMINDER.sub("", text_of(content)).strip()
        if not text:
            return
        if "[Request interrupted by user" in text:
            self.counts["interrupts"] += 1
            self.flush_tools()
            self.timeline.append(("I", "interrumpido por el usuario"))
            return
        if origin == "task-notification":
            self.counts["task_notifications"] += 1
            return
        if origin == "peer":
            self.counts["peer_messages"] += 1
            self.flush_tools()
            self.timeline.append(("P", text))
            return
        for name in COMMAND_NAME.findall(text):
            self.slash_commands[name] += 1
        if text.startswith("<") or text.startswith("Caveat:"):
            return
        self.prompts.append(text)
        if CORRECTION.match(text):
            self.counts["corrections"] += 1
        self.flush_tools()
        self.timeline.append(("U", text))

    def facts(self) -> dict:
        repo, subdir = repo_of(self.cwd or self.project_dir)
        stamps = sorted(self.stamps)
        return {
            "session_id": self.id,
            "user": os.environ.get("DIK_USER") or getpass.getuser(),
            "repo": repo,
            "subdir": subdir,
            "title": self.title or self.ai_title,
            "started_at": stamps[0].isoformat() if stamps else None,
            "ended_at": stamps[-1].isoformat() if stamps else None,
            "active_minutes": active_minutes(stamps),
            "prompts": len(self.prompts),
            "first_prompt": scrub(self.prompts[0], 300) if self.prompts else "",
            "corrections": self.counts["corrections"],
            "interrupts": self.counts["interrupts"],
            "compactions": self.counts["compactions"],
            "compactions_auto": self.counts["compactions_auto"],
            "tool_calls": sum(self.tools.values()),
            "tool_errors": self.counts["tool_errors"],
            "denials": self.counts["denials"],
            "api_errors": self.counts["api_errors"],
            "task_notifications": self.counts["task_notifications"],
            "peer_messages": self.counts["peer_messages"],
            "input_tokens": self.counts["input_tokens"],
            "output_tokens": self.counts["output_tokens"],
            "models": dict(self.models),
            "tools": dict(self.tools),
            "canon": dict(self.canon),
            "mcp_servers": dict(self.mcp),
            "skills": dict(self.skills),
            "slash_commands": dict(self.slash_commands),
            "subagents": dict(self.subagents),
            "subagent_tools": dict(self.subagent_tools),
            "hosts": dict(self.hosts),
            "sequence": self.sequence,
        }

    def condensed(self, facts: dict) -> str:
        self.flush_tools()
        top_tools = ", ".join(f"{k} {v}" for k, v in Counter(facts["canon"]).most_common(12))
        header = (
            f"repo: {facts['repo']}{'/' + facts['subdir'] if facts['subdir'] else ''}\n"
            f"título: {facts['title'] or '-'}\n"
            f"fecha: {(facts['started_at'] or '')[:10]} · minutos activos: {facts['active_minutes']} · "
            f"prompts: {facts['prompts']} · llamadas a herramientas: {facts['tool_calls']} · "
            f"errores: {facts['tool_errors']} · compactaciones: {facts['compactions']} · "
            f"interrupciones: {facts['interrupts']}\n"
            f"herramientas más usadas: {top_tools or '-'}\n"
            f"skills: {facts['skills'] or '-'} · subagentes: {facts['subagents'] or '-'} · "
            f"slash: {facts['slash_commands'] or '-'}\n"
        )
        for level in (0, 1, 2):
            body = self.render(level)
            if len(header) + len(body) <= CONDENSED_LIMIT or level == 2:
                break
        if len(header) + len(body) > CONDENSED_LIMIT:
            room = CONDENSED_LIMIT - len(header) - 80
            head, tail = body[: int(room * 0.35)], body[-int(room * 0.65) :]
            cut = len(body) - len(head) - len(tail)
            body = f"{head}\n[… recortados {cut} caracteres del medio …]\n{tail}"
        return header + "\n" + body

    def render(self, level: int) -> str:
        """Level 0 keeps everything; 1 drops intermediate assistant text; 2 also shortens prompts, tools."""
        lines = []
        items = self.timeline
        for i, (kind, text) in enumerate(items):
            final = kind == "A" and (i + 1 == len(items) or items[i + 1][0] in ("U", "C", "I"))
            if kind == "A":
                if level >= 1 and not final:
                    continue
                limit = FINAL_TEXT_LIMIT if final else TEXT_LIMIT
                limit = 600 if level >= 2 else limit
            elif kind == "U":
                limit = 500 if level >= 2 else PROMPT_LIMIT
            elif kind == "T":
                limit = 200 if level >= 2 else 700
            elif kind == "E":
                limit = ERROR_LIMIT
            else:
                limit = 200
            lines.append(f"[{kind}] {scrub(' '.join(text.split()), limit)}")
        return "\n".join(lines)


def read_session(path: str) -> Session | None:
    session_id = os.path.splitext(os.path.basename(path))[0]
    session = Session(session_id, os.path.basename(os.path.dirname(path)))
    with open(path, errors="ignore") as f:
        for line in f:
            try:
                session.add(json.loads(line))
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
    sub_dir = os.path.join(os.path.dirname(path), session_id, "subagents")
    for sub_path in glob.glob(os.path.join(sub_dir, "*.jsonl")):
        with open(sub_path, errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = (d.get("message") or {}).get("content")
                if d.get("type") == "assistant" and isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_use":
                            session.subagent_tools[canon(block.get("name", "?"), block.get("input"))[0]] += 1
    return session if session.stamps else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--out", default="data/backfill")
    args = parser.parse_args()

    os.makedirs(os.path.join(args.out, "condensed"), exist_ok=True)
    paths = sorted(glob.glob(os.path.join(args.projects, "*", "*.jsonl")))
    written = 0
    with open(os.path.join(args.out, "facts.jsonl"), "w") as out:
        for path in paths:
            session = read_session(path)
            if not session:
                continue
            facts = session.facts()
            out.write(json.dumps(facts, ensure_ascii=False) + "\n")
            if facts["prompts"]:
                with open(os.path.join(args.out, "condensed", f"{session.id}.txt"), "w") as f:
                    f.write(session.condensed(facts))
            written += 1
    print(f"{written} sessions from {len(paths)} transcripts -> {args.out}")


if __name__ == "__main__":
    main()
