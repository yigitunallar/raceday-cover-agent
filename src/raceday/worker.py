"""The agent process. Run it, kill it, run it again: nothing happens twice.

    python -m raceday.worker
"""
import time

from . import db as dbm
from .agent.graph import build, run_task

OPEN = ["new", "priced", "awaiting_owner", "confirmed", "charged", "issued", "reported", "blocked"]


def main(poll_seconds: float = 1.0) -> None:
    dbm.ensure_indexes()
    graph = build()
    print("agent started", flush=True)
    while True:
        # Triage: the horse that leaves the stable soonest goes first.
        tasks = list(dbm.db()[dbm.TASKS].find({"status": {"$in": OPEN}}).sort("deadline", 1))
        for task in tasks:
            before = task["status"]
            result = run_task(graph, task["_id"])
            if result.get("status") != before:
                print(f"{task['_id']}: {before} -> {result.get('status')}", flush=True)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
