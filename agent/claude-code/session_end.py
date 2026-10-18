"""SessionEnd hook: measures the session that just closed, on this machine, without making the close wait.

The hook only starts a detached process and returns. That process appends the session's facts
(tools/session_facts.py, no LLM) to ~/.claude/session-insights/facts.jsonl and its work card
(tools/work_card_prompt.md through `claude -p` with a small model) to cards.jsonl. Problems go to
errors.log next to them; the hook itself never fails. Nothing goes to the team server. Standard library only.

Hook command:  python3 /path/to/hackatonbuild101/agent/claude-code/session_end.py
Environment:   BUILD101_INSIGHTS_DIR (default ~/.claude/session-insights), BUILD101_INSIGHTS_USER
               (default $USER), BUILD101_CARD_MODEL (default haiku), BUILD101_INSIGHTS_CARDS=0 for facts only
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
# Card runs (`claude -p`) and DIK's evaluator are sessions too; measuring them would loop
CHILD_MARKERS = ("BUILD101_INSIGHTS_CHILD", "DIK_EVALUATOR")
# Session ids name files here, so only plain ids are accepted
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def insights_dir(environ) -> Path:
    return Path(os.path.expanduser(environ.get("BUILD101_INSIGHTS_DIR", "~/.claude/session-insights")))


def log_error(folder: Path, session_id: str, message: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "errors.log", "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {session_id} {message[:300]}\n")


def start_worker(raw: dict, environ, popen=subprocess.Popen) -> bool:
    """Start the measuring process detached from Claude Code: own session, no pipes back to the hook."""
    session_id = raw.get("session_id")
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return False
    if any(environ.get(m) for m in CHILD_MARKERS):
        return False
    args = [sys.executable, str(Path(__file__).resolve()), "--worker", session_id]
    if isinstance(raw.get("transcript_path"), str):
        args.append(raw["transcript_path"])
    popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return True


def claim(folder: Path, session_id: str) -> Path | None:
    """One measurement per close, even if the hook is installed both for the user and for the project."""
    lock = folder / "locks" / session_id
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime > 600:
        lock.unlink(missing_ok=True)  # left behind by a run that died
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return None
    return lock


def measure(session_id: str, transcript: str | None, environ) -> None:
    folder = insights_dir(environ)
    lock = claim(folder, session_id)
    if not lock:
        return
    try:
        _measure(session_id, transcript, environ, folder)
    finally:
        lock.unlink(missing_ok=True)


def _measure(session_id: str, transcript: str | None, environ, folder: Path) -> None:
    sys.path.insert(0, str(TOOLS))
    import session_facts
    import work_card

    path = (
        transcript if transcript and os.path.exists(transcript) else session_facts.transcript_of(session_id)
    )
    if not path:
        log_error(folder, session_id, "no transcript")
        return
    user = environ.get("BUILD101_INSIGHTS_USER") or environ.get("USER", "me")
    facts = session_facts.facts_for(path, user)
    if not facts:
        return
    work_card.append_line(str(folder / "facts.jsonl"), json.loads(session_facts.dumps(facts)))
    if environ.get("BUILD101_INSIGHTS_CARDS", "1") == "0":
        return
    try:
        card = work_card.make_card(facts, environ.get("BUILD101_CARD_MODEL", work_card.MODEL))
    except Exception as error:
        log_error(folder, session_id, f"card: {error}")
        return
    work_card.append_line(str(folder / "cards.jsonl"), card)


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        session_id = sys.argv[2]
        try:
            measure(session_id, sys.argv[3] if len(sys.argv) > 3 else None, os.environ)
        except Exception as error:
            log_error(insights_dir(os.environ), session_id, f"facts: {error!r}")
        return
    try:
        raw = json.load(sys.stdin)
        if isinstance(raw, dict):
            start_worker(raw, os.environ)
    except Exception:
        # Closing a session must never fail or wait because measuring it did
        pass


if __name__ == "__main__":
    main()
