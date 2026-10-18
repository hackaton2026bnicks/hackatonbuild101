"""Polls the team's messages and prints one line per new one, for a bridge session to watch.

Run it under Claude Code's Monitor tool so each line becomes a notification. Standard library only.

Environment:  BUILD101_KEY (required), BUILD101_URL (optional, the API base), BUILD101_USER (your user,
              to skip your own messages), BUILD101_REPO (optional, only this repo and team-wide messages)
Usage:        python3 -u tools/bridge_listen.py [--since ID] [--interval SECONDS]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_API = "https://54-89-124-87.sslip.io/api"
HOOK_PATH = "/hooks/claude-code"
# Notices the server already injects into the working sessions themselves
SKIP_KINDS = {"overlap", "hint", "workspace"}


def fetch(api: str, key: str) -> list[dict]:
    request = urllib.request.Request(f"{api}/messages?limit=200", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["messages"]


def relevant(message: dict, me: str, repo: str) -> bool:
    if message.get("from_user") == me or message.get("kind") in SKIP_KINDS:
        return False
    topics = message.get("topics") or []
    repos = [t for t in topics if t.startswith("repo:")]
    return not repo or not repos or f"repo:{repo}" in repos


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=int, help="last message id already seen; default: start from now")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    # A Windows console is cp1252: one "→" in a message would raise and that message would be lost
    sys.stdout.reconfigure(encoding="utf-8")
    key = os.environ.get("BUILD101_KEY")
    if not key:
        print("BRIDGE_ERROR BUILD101_KEY is not set", flush=True)
        return 1
    api = os.environ.get("BUILD101_URL", DEFAULT_API).rstrip("/")
    # The hooks' BUILD101_URL is the hook endpoint; the API base is what comes before it
    api = api[: -len(HOOK_PATH)] if api.endswith(HOOK_PATH) else api
    me, repo = os.environ.get("BUILD101_USER", ""), os.environ.get("BUILD101_REPO", "")

    last, failing = args.since, False
    while True:
        try:
            rows = sorted(fetch(api, key), key=lambda m: m["id"])
            if last is None:
                last = rows[-1]["id"] if rows else 0
                print(f"BRIDGE_READY listening from message #{last}", flush=True)
            for message in rows:
                if message["id"] <= last:
                    continue
                last = message["id"]
                if relevant(message, me, repo):
                    fields = ("id", "kind", "from_user", "reply_to", "topics", "created_at")
                    item = {k: message.get(k) for k in fields}
                    item["body"] = html.unescape(message.get("body") or "")
                    print("BRIDGE_MSG " + json.dumps(item, ensure_ascii=False), flush=True)
            failing = False
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            if not failing:  # report once per outage, not every retry
                print(f"BRIDGE_ERROR {type(error).__name__}: {error}", flush=True)
            failing = True
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
