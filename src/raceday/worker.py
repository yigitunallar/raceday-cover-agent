"""The agent process. Run it, kill it, run it again: nothing happens twice.

    python -m raceday.worker

Several horses are worked on at once (WORKER_CONCURRENCY, default 6): most of
a horse's time is spent waiting on the model, so threads overlap that waiting.
Crash safety does not depend on this loop: every outside write goes through the
ledger (operation ID per task and step, read back before any re-send), and a
task is never run twice at the same time inside one process.
"""
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor

from . import db as dbm
from .agent.graph import build, run_task

OPEN = ["new", "priced", "awaiting_owner", "confirmed", "charged", "issued", "reported", "blocked"]
CONCURRENCY = max(1, int(os.environ.get("WORKER_CONCURRENCY", "6")))


def _run_one(graph, task_id: str, before: str) -> None:
    try:
        result = run_task(graph, task_id)
        if result.get("status") != before:
            print(f"{task_id}: {before} -> {result.get('status')}", flush=True)
    except Exception as e:  # noqa: BLE001 - one horse's failure must not stop the others
        print(f"{task_id}: error {e!r}; will retry on the next poll", flush=True)


def main(poll_seconds: float = 1.0, concurrency: int = CONCURRENCY) -> None:
    dbm.ensure_indexes()
    graph = build()  # one compiled graph and one MongoDBSaver, shared: both are stateless per call
    print(f"agent started ({concurrency} at a time)", flush=True)
    in_flight: dict[str, Future] = {}  # touched only by this loop's thread
    last_done: dict[str, float] = {}  # a horse that just finished waits one poll before it runs again
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="horse") as pool:
        while True:
            now = time.monotonic()
            for tid in [t for t, f in in_flight.items() if f.done()]:
                del in_flight[tid]  # never the same task twice at once: skip what is still running
                last_done[tid] = now
            last_done = {t: at for t, at in last_done.items() if now - at < poll_seconds}
            resting = list(last_done)
            free = concurrency - len(in_flight)
            if free > 0:
                # Triage: the horse that leaves the stable soonest goes first.
                tasks = dbm.db()[dbm.TASKS].find({"status": {"$in": OPEN}, "_id": {"$nin": list(in_flight) + resting}},
                                                 {"status": 1}).sort("deadline", 1).limit(free)
                for task in tasks:
                    in_flight[task["_id"]] = pool.submit(_run_one, graph, task["_id"], task["status"])
            # While horses are in flight, look back soon so a freed slot is refilled quickly.
            time.sleep(min(poll_seconds, 0.25) if in_flight else poll_seconds)


if __name__ == "__main__":
    main()
