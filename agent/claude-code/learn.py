"""SessionEnd / PreCompact hook: turns what a session learned into lessons for the team vault.

The working agent rarely stops to share what it learned, so this does it afterwards, on this machine,
with the whole transcript: it condenses the part of the session not read yet, and only if it shows a
signal (an error that got solved, a correction from the user, a "remember this") asks a small model
(tools/knowledge_prompt.md through `claude -p`) for 0 to 3 lessons. They wait in the team server's queue
(POST /lessons) until the archivista (agent/hermes/bandeja.py) files them as notes in build101-vault, or
discards them. From there the hooks hand them to whoever touches the same thing (server/api/vault.py).

The transcript never leaves the machine: only the lessons, scrubbed here and again on the server.
The hook only starts a detached process and returns; problems go to ~/.claude/build101/learn.log.
Standard library only, Python 3.9+.

Hook command:  python3 /path/to/hackatonbuild101/agent/claude-code/learn.py
Environment:   BUILD101_KEY (required), BUILD101_URL, BUILD101_REPO, BUILD101_LEARN_MODEL (default haiku),
               BUILD101_LEARN=0 to turn it off
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "tools"
sys.path.insert(0, str(HERE))
from forward import clean, env_secrets, read_stdin_json  # noqa: E402

PROMPT = TOOLS / "knowledge_prompt.md"
STATE_DIR = Path(os.path.expanduser("~/.claude/build101"))
MODEL = "haiku"
TIMEOUT = 180
# Runs started by this script or by DIK's evaluators are sessions too; learning from them would loop
CHILD_MARKERS = ("BUILD101_INSIGHTS_CHILD", "DIK_EVALUATOR", "BUILD101_LEARN_CHILD")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
MAX_UNITS = 3
EPISODE_CHARS = 14000
WINDOW = 6  # lines kept around each signal
TEXT_CHARS = 300
# The vault's note types (reglas/AGENTS.md of build101-vault); anything else is left to the archivista
TYPES = ("gotcha", "receta", "decision", "criterio", "recurso")
LISTS = ("dead_ends", "files", "hosts", "keywords")
FUENTE = "sesión de Claude Code (learn.py)"

CORRECTION = re.compile(
    r"(?i)(^no[,.!]|\beso no\b|\best[aá] mal\b|\bno es as[ií]\b|\bte equivocaste\b|\bsiempre\b|\bnunca\b"
    r"|\bthat'?s (wrong|not)\b|\balways\b|\bnever\b|\bdon'?t\b)"
)
REMEMBER = re.compile(
    r"(?i)\b(acordate|record[aá]|anot[aá]|guard[aá] (esto|que)|la pr[oó]xima|remember|note that)\b"
)


def short(text: str, secrets: list[str], limit: int = TEXT_CHARS) -> str:
    return " ".join(clean(text, secrets).split())[:limit]


def tool_line(name: str, args: dict, secrets: list[str]) -> str:
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "skill", "subagent_type"):
        if isinstance(args.get(key), str):
            return f"TOOL {name}: {short(args[key], secrets, 200)}"
    return f"TOOL {name}"


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


def condense(lines: list[str], secrets: list[str]) -> tuple[list[str], list[int]]:
    """One readable line per prompt, answer, tool call and error; and the indexes of the signals in them.

    A signal is a user correction or "remember this", or an error whose tool later succeeded.
    """
    out: list[str] = []
    signals: list[int] = []
    tool_names: dict[str, str] = {}
    failing: dict[str, int] = {}  # tool name -> index of its last unsolved error
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "user":
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and not block.get("text", "").lstrip().startswith("<"):
                    text = block["text"]
                    out.append("USER: " + short(text, secrets, 500))
                    if CORRECTION.search(text) or REMEMBER.search(text):
                        signals.append(len(out) - 1)
                elif block.get("type") == "tool_result":
                    name = tool_names.get(block.get("tool_use_id"), "tool")
                    if block.get("is_error"):
                        out.append(f"ERROR ({name}): " + short(result_text(block.get("content")), secrets))
                        failing[name] = len(out) - 1
                    elif name in failing:
                        signals.append(failing.pop(name))
                        out.append(f"OK ({name}) after the error above")
        elif entry.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append("ASSISTANT: " + short(block["text"], secrets))
                elif block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    tool_names[block.get("id")] = name
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    out.append(tool_line(name, args, secrets))
    return out, sorted(set(signals))


def episode(out: list[str], signals: list[int]) -> str:
    """The lines around each signal, plus the first prompts for context, within EPISODE_CHARS."""
    keep = {i for i, line in enumerate(out[:40]) if line.startswith("USER: ")}
    keep = set(sorted(keep)[:2])
    for i in signals:
        keep.update(range(max(0, i - WINDOW), min(len(out), i + WINDOW + 1)))
    text, last = [], -1
    for i in sorted(keep):
        if last >= 0 and i != last + 1:
            text.append("...")
        text.append(out[i])
        last = i
    joined = "\n".join(text)
    return joined[-EPISODE_CHARS:]


def parse_units(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except ValueError:
        return []
    units = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) and item[k].strip() for k in ("title", "body")
        ):
            continue
        applies = item.get("aplica_cuando")
        units.append(
            {
                "type": item.get("type") if item.get("type") in TYPES else None,
                "title": item["title"].strip()[:200],
                "body": item["body"].strip()[:2000],
                "aplica_cuando": applies.strip()[:300] if isinstance(applies, str) else "",
                **{k: [str(v)[:300] for v in item.get(k) or [] if isinstance(v, str)][:5] for k in LISTS},
            }
        )
    return units[:MAX_UNITS]


def child_env() -> dict:
    """The model run uses the user's own CLI login, never reaches the team server, is not learned from."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k == "CLAUDE_CODE_OAUTH_TOKEN"
        or not k.startswith(
            ("CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_AGENT_SDK_", "ANTHROPIC_BASE_URL", "BUILD101_KEY")
        )
    }
    env["BUILD101_LEARN_CHILD"] = "1"
    return env


def ask_model(text: str, model: str, run=subprocess.run) -> list[dict]:
    # On Windows the CLI is claude.cmd, which mangles multi-line arguments: the prompt goes on stdin
    cli = shutil.which("claude") or "claude"
    cmd = [
        cli, "-p", "--model", model, "--output-format", "json", "--no-session-persistence",
        "--setting-sources", "project", "--strict-mcp-config", "--tools", "",
    ]  # fmt: skip
    prompt = PROMPT.read_text(encoding="utf-8") + "\n\n<tramo>\n" + text + "\n</tramo>"
    with tempfile.TemporaryDirectory() as workdir:  # no CLAUDE.md or settings from any repo
        out = run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=workdir,
            env=child_env(),
            timeout=TIMEOUT,
        )
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[-300:])
    return parse_units(json.loads(out.stdout).get("result", ""))


def read_new_lines(transcript: str, session_id: str) -> tuple[list[str], int]:
    """Lines added since the last run for this session, and the new offset to remember."""
    state = load_state()
    done = state.get(session_id, 0)
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return lines[done:], len(lines)


def load_state() -> dict:
    try:
        return json.loads((STATE_DIR / "learned.json").read_text())
    except (OSError, ValueError):
        return {}


def save_offset(session_id: str, offset: int) -> None:
    state = load_state()
    state[session_id] = offset
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "learned.json").write_text(json.dumps(state))


def log(session_id: str, message: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "learn.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {session_id} {message[:300]}\n")


def learn(session_id: str, transcript: str, repo: str, environ: dict, ask=ask_model, send=None) -> int:
    """Propose what the unread part of the session learned. Returns how many lessons were sent."""
    from mcp_server import request  # same key, URL and error handling as the agent's tools

    # PreCompact and SessionEnd can fire close together; one run per session at a time
    lock = STATE_DIR / "locks" / session_id
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime > 600:
        lock.unlink()  # left behind by a run that died
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return 0
    try:
        return _learn(
            session_id,
            transcript,
            repo,
            environ,
            ask,
            send or (lambda body: request("POST", "/lessons", environ, body)),
        )
    finally:
        lock.unlink(missing_ok=True)


def _learn(session_id: str, transcript: str, repo: str, environ: dict, ask, send) -> int:
    lines, offset = read_new_lines(transcript, session_id)
    secrets = env_secrets(environ)
    out, signals = condense(lines, secrets)
    if not signals:
        save_offset(session_id, offset)
        return 0
    units = ask(episode(out, signals), environ.get("BUILD101_LEARN_MODEL", MODEL))
    for unit in units:
        send(lesson_for(unit, repo, session_id, secrets))
    save_offset(session_id, offset)
    return len(units)


def lesson_for(unit: dict, repo: str, session_id: str, secrets: list[str]) -> dict:
    """A unit as the queue takes it: the text for the archivista, and what it suggests about the note."""
    text = unit["body"]
    if unit["dead_ends"]:
        text += "\nLo que parecía y no era: " + "; ".join(unit["dead_ends"]) + "."
    if unit["hosts"]:
        text += "\nHosts: " + ", ".join(unit["hosts"]) + "."
    applies = unit["aplica_cuando"] or ", ".join(unit["keywords"])
    lesson = {
        "texto": clean(text, secrets)[:4000],
        "titulo": clean(unit["title"], secrets),
        "aplica_cuando": clean(applies, secrets)[:300] or None,
        "archivos": unit["files"],
        "fuente": FUENTE,
        "repo": repo,
        "session_id": session_id,
    }
    if unit["type"]:
        lesson["tipo"] = unit["type"]
    return lesson


def start_worker(raw: dict, environ, popen=subprocess.Popen) -> bool:
    session_id, transcript = raw.get("session_id"), raw.get("transcript_path")
    if (
        not isinstance(session_id, str)
        or not SESSION_ID.fullmatch(session_id)
        or not isinstance(transcript, str)
    ):
        return False
    if environ.get("BUILD101_LEARN") == "0" or not environ.get("BUILD101_KEY"):
        return False
    if any(environ.get(marker) for marker in CHILD_MARKERS):
        return False
    project = environ.get("CLAUDE_PROJECT_DIR") or raw.get("cwd") or ""
    repo = (environ.get("BUILD101_REPO") or Path(project).name or "unknown")[:200]
    popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", session_id, transcript, repo],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return True


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        session_id, transcript, repo = sys.argv[2:5]
        try:
            sent = learn(session_id, transcript, repo, dict(os.environ))
            if sent:
                log(session_id, f"proposed {sent} lesson(s)")
        except Exception as error:
            log(session_id, f"failed: {error!r}")
        return
    try:
        raw = read_stdin_json()
        if isinstance(raw, dict):
            start_worker(raw, os.environ)
    except Exception:
        # Closing or compacting a session must never fail or wait because learning from it did
        pass


if __name__ == "__main__":
    main()
