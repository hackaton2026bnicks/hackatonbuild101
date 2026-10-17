"""Send a short scripted agent session to the server, to see the dashboard and feed working.

Usage:
    python tools/simulate_agents.py --url http://localhost/api --key b101_...
    python tools/simulate_agents.py --url https://<host>.sslip.io/api --key b101_... --delay 2
"""

import argparse
import json
import time
import urllib.request
import uuid

SCRIPT = [
    ("session_start", {"source": "startup"}),
    ("intent", {"prompt": "integrate PSD2 payment initiation with bankx"}),
    ("pre_action", {"tool": "bash", "canonical": "bash(curl POST {bankx}/oauth/token)"}),
    ("post_action", {"tool": "bash", "canonical": "bash(curl POST {bankx}/oauth/token)", "exit_code": 0}),
    ("pre_action", {"tool": "edit", "canonical": "edit(payments/bank_client)"}),
    ("pre_action", {"tool": "bash", "canonical": "bash(curl POST {bankx}/v1/payments)"}),
    ("post_action", {"tool": "bash", "canonical": "error(HTTP401, invalid_token)", "exit_code": 1}),
    ("session_end", {"reason": "exit"}),
]


def send(url: str, key: str, events: list[dict]) -> dict:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/events",
        data=json.dumps({"events": events}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", required=True, help="API base URL, ending in /api")
    parser.add_argument("--key", required=True, help="your API key")
    parser.add_argument("--agent", default="claude-code")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between events")
    args = parser.parse_args()

    session_id = f"sim-{uuid.uuid4().hex[:8]}"
    for event_type, payload in SCRIPT:
        event = {
            "agent": args.agent,
            "session_id": session_id,
            "type": event_type,
            "payload": payload,
            "ts": time.time(),
        }
        result = send(args.url, args.key, [event])
        print(f"{event_type:<14} {payload.get('canonical', '')} -> {result}")
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
