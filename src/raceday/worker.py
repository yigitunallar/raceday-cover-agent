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
from datetime import datetime, timezone
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


def _log_resume() -> None:
    """On start, any unproven ledger action for an open task means we crashed mid-write: say so."""
    try:
        d = dbm.db()
        # Investigation locks belong to the process that took them. A fresh worker means that process is
        # gone (killed), so its locks are released now instead of blocking those horses for 90 s.
        d[dbm.TASKS].update_many({"investigating_since": {"$ne": None}}, {"$set": {"investigating_since": None}})
        open_ids = [t["_id"] for t in d[dbm.TASKS].find({"status": {"$in": OPEN}}, {"_id": 1})]
        steps: dict[str, list[str]] = {}
        for a in d[dbm.ACTIONS].find({"task_id": {"$in": open_ids}, "state": {"$ne": "verified"}},
                                     {"task_id": 1, "step": 1}):
            steps.setdefault(a["task_id"], []).append(a.get("step"))
        for task_id, s in steps.items():
            d[dbm.TIMELINE].insert_one({
                "task_id": task_id, "at": datetime.now(timezone.utc), "kind": "resume_after_crash", "duration_ms": None,
                "detail": {"system": "agent", "steps": s, "message": "Agent restarted: reading back before acting"}})
    except Exception as e:  # noqa: BLE001 - never stop the agent from starting
        print(f"resume log error {e!r}", flush=True)


def main(poll_seconds: float = 1.0, concurrency: int = CONCURRENCY) -> None:
    dbm.ensure_indexes()
    _log_resume()
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
