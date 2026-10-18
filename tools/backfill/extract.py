"""Run the DIK v2 extractor over condensed sessions: one `claude -p` call per session returns a work card
and 0..3 knowledge unit candidates as JSON. Resumable: sessions that already have a card are skipped.

Usage: python3 tools/backfill/extract.py [--out data/backfill] [--workers 6] [--model sonnet] [--limit N]
                                        [ids...]
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 300


def clean_env() -> dict:
    # Without the desktop app's own proxy and auth variables, the CLI uses its login (same as DIK's evaluator)
    env = {
        k: v
        for k, v in os.environ.items()
        if k == "CLAUDE_CODE_OAUTH_TOKEN"
        or not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_AGENT_SDK_", "ANTHROPIC_BASE_URL"))
    }
    env["DIK_EVALUATOR"] = "1"
    return env


def parse_card(text: str) -> dict:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in output")
    return json.loads(text[start : end + 1])


def extract(session_id: str, args, system_prompt: str, workdir: str, env: dict) -> tuple[str, str]:
    condensed = open(os.path.join(args.out, "condensed", f"{session_id}.txt")).read()
    cmd = [
        "claude",
        "-p",
        "--model",
        args.model,
        "--output-format",
        "json",
        "--no-session-persistence",
        "--setting-sources",
        "project",  # no user hooks: these runs must not be captured or evaluated
        "--strict-mcp-config",
        "--tools",
        "",
        "--system-prompt",
        system_prompt,
    ]
    last_error = ""
    for attempt in range(2):
        started = time.time()
        try:
            out = subprocess.run(
                cmd,
                input=f"<session>\n{condensed}\n</session>",
                capture_output=True,
                text=True,
                cwd=workdir,
                env=env,
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            last_error = "timeout"
            continue
        if out.returncode != 0:
            last_error = (out.stderr or out.stdout).strip()[-400:]
            continue
        try:
            envelope = json.loads(out.stdout)
            card = parse_card(envelope.get("result", ""))
        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"parse: {e}: {out.stdout[-400:]}"
            continue
        record = {
            "session_id": session_id,
            "card": card,
            "cost_usd": envelope.get("total_cost_usd"),
            "seconds": round(time.time() - started, 1),
            "model": args.model,
            "attempt": attempt + 1,
        }
        with open(os.path.join(args.out, "cards", f"{session_id}.json"), "w") as f:
            json.dump(record, f, ensure_ascii=False, indent=1)
        return session_id, "ok"
    with open(os.path.join(args.out, "cards", f"{session_id}.err"), "w") as f:
        f.write(last_error)
    return session_id, f"error: {last_error[:200]}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--out", default="data/backfill")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default=os.environ.get("DIK_EXTRACT_MODEL", "sonnet"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    os.makedirs(os.path.join(args.out, "cards"), exist_ok=True)
    user = os.environ.get("DIK_USER") or getpass.getuser()
    system_prompt = open(os.path.join(HERE, "extractor_prompt.md")).read().replace("{{USER}}", user)
    condensed = glob.glob(os.path.join(args.out, "condensed", "*.txt"))
    ids = args.ids or sorted(os.path.splitext(os.path.basename(p))[0] for p in condensed)
    todo = [i for i in ids if not os.path.exists(os.path.join(args.out, "cards", f"{i}.json"))]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} sessions to extract ({len(ids) - len(todo)} already done)", flush=True)

    workdir = tempfile.mkdtemp(prefix="dik-extract-")
    env = clean_env()
    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(extract, i, args, system_prompt, workdir, env) for i in todo]
        for future in as_completed(futures):
            session_id, status = future.result()
            done += 1
            failed += status != "ok"
            print(f"[{done}/{len(todo)}] {session_id[:8]} {status}", flush=True)
    print(f"finished: {done - failed} ok, {failed} failed", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
