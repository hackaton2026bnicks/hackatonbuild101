"""Background worker: distiller and criteria miner.

For now it only logs a heartbeat so the container can be deployed and monitored.
"""

import logging
import os
import signal
import threading

HEARTBEAT_SECONDS = 60

log = logging.getLogger("worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    version = os.environ.get("GIT_SHA", "dev")
    has_llm_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    log.info("worker started version=%s llm_key=%s", version, has_llm_key)

    while not stop.wait(HEARTBEAT_SECONDS):
        log.info("heartbeat")

    log.info("worker stopped")


if __name__ == "__main__":
    main()
