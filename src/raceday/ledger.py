"""The guarded write. Every change to an outside system goes through here.

Guard -> Intent -> Call -> Read back -> Record.

The model decides what to do next. This code decides whether it is allowed
and whether it worked. Nothing is re-sent blindly: after a crash we read back
first, and only "verified" lets a task move on.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pymongo.errors import DuplicateKeyError

from . import db as dbm


class Outcome(str, Enum):
    VERIFIED = "verified"
    NOT_FOUND = "not_found"
    MISMATCHED = "mismatched"
    UNKNOWN = "unable_to_determine"
    BLOCKED = "blocked_by_guard"


def now() -> datetime:
    return datetime.now(timezone.utc)


def operation_id(task_id: str, step: str) -> str:
    """Stable per task and step, so a restarted agent finds its own earlier attempt."""
    return f"{task_id}:{step}"


def guarded_write(
    *,
    task_id: str,
    step: str,
    system: str,
    intended: dict[str, Any],
    guard: Callable[[], str | None],
    call: Callable[[str, dict[str, Any]], Any],
    read_back: Callable[[str], dict[str, Any] | None],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run one write safely. Returns the ledger record.

    guard()               -> None if allowed, or the reason it is not.
    call(op_id, intended) -> sends the request. May raise; the answer is not trusted.
    read_back(op_id)      -> what the target system holds for this op_id, None if nothing.
                             Raises if the system cannot be asked.
    """
    actions = dbm.db()[dbm.ACTIONS]
    op_id = operation_id(task_id, step)

    existing = actions.find_one({"operation_id": op_id})
    if existing and existing["state"] == Outcome.VERIFIED:
        return existing  # proven earlier: never do it twice

    if not existing:
        reason = guard()
        if reason:
            return {"operation_id": op_id, "task_id": task_id, "step": step, "state": Outcome.BLOCKED, "reason": reason}
        try:
            # Intent is saved BEFORE the call. If this save fails we stop here.
            actions.insert_one({
                "operation_id": op_id, "task_id": task_id, "step": step, "system": system,
                "intended": intended, "state": "intent", "attempts": 0, "evidence": None,
                "created_at": now(), "updated_at": now(),
            })
        except DuplicateKeyError:
            pass  # another run saved the intent first; fall through and read back

    sent = False
    for _ in range(max_attempts):
        # Read back FIRST: an earlier attempt may have landed even though we never heard.
        outcome, evidence = _check(read_back, op_id, intended)
        if outcome == Outcome.VERIFIED:
            if existing and existing.get("state") == "intent" and not sent:
                return _recovered(op_id, task_id, step, system, evidence)
            return _record(op_id, outcome, evidence)
        if outcome == Outcome.MISMATCHED:
            return _record(op_id, outcome, evidence)  # never paper over a wrong result
        if outcome == Outcome.NOT_FOUND:
            actions.update_one({"operation_id": op_id}, {"$inc": {"attempts": 1}, "$set": {"updated_at": now()}})
            sent = True
            try:
                call(op_id, intended)
            except Exception:  # noqa: BLE001 - the call's own answer is never trusted
                pass
        # UNKNOWN: loop and ask again
    outcome, evidence = _check(read_back, op_id, intended)
    if outcome == Outcome.VERIFIED and existing and existing.get("state") == "intent" and not sent:
        return _recovered(op_id, task_id, step, system, evidence)
    return _record(op_id, outcome, evidence)


def _recovered(op_id: str, task_id: str, step: str, system: str, evidence) -> dict[str, Any]:
    """An intent from an earlier run was found already held by the system: done before the crash."""
    if isinstance(evidence, dict):
        evidence = {**evidence, "found_on_read_back": True}
    try:
        dbm.db()[dbm.TIMELINE].insert_one({
            "task_id": task_id, "at": now(), "kind": "recovered", "duration_ms": None,
            "detail": {"system": system, "step": step,
                       "message": "Already done before the crash: read back, not sent again"}})
    except Exception:  # noqa: BLE001 - the timeline never blocks the ledger
        pass
    return _record(op_id, Outcome.VERIFIED, evidence)


def _check(read_back, op_id: str, intended: dict[str, Any]):
    try:
        found = read_back(op_id)
    except Exception as exc:  # noqa: BLE001
        return Outcome.UNKNOWN, {"error": str(exc)[:200]}
    if found is None:
        return Outcome.NOT_FOUND, None
    held = found.get("payload", found)
    wrong = {k: {"intended": v, "held": held.get(k)} for k, v in intended.items() if held.get(k) != v}
    if wrong:
        return Outcome.MISMATCHED, {"found": found, "differences": wrong}
    return Outcome.VERIFIED, found


def _record(op_id: str, outcome: Outcome, evidence) -> dict[str, Any]:
    actions = dbm.db()[dbm.ACTIONS]
    actions.update_one(
        {"operation_id": op_id},
        {"$set": {"state": outcome.value, "evidence": evidence, "updated_at": now()}},
    )
    return actions.find_one({"operation_id": op_id})
