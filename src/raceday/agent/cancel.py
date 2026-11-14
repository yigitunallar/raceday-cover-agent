"""The owner cancels. Before the horse leaves the stable: refund and cancel the policy.
After it has left: cover has started, so no refund.

The decision is made once, at the first request, and saved on the task before
anything else happens. Everything after that is the same ledger pattern as the
rest of the agent (intent -> call with an operation ID -> read back -> verified),
so a repeat request, or a request after a crash, finishes the job and never
refunds twice.

The refund uses the same step name ("refund") as the graph's refund-and-escalate
path: one operation ID per task, so at most one refund per task, whoever asks.
"""
from datetime import datetime, timezone
from typing import Any, Callable

from .. import db as dbm
from ..ledger import Outcome, guarded_write, operation_id

REFUND = "refund"
REFUSED = "refused"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_left(deadline: datetime, now: datetime) -> bool:
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now >= deadline


def decide(task: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The product rule, as data. Pure: no database, no network."""
    if has_left(task["deadline"], now):
        return {"decision": REFUSED, "reason": "the horse has left the stable: cover has started, no refund"}
    return {"decision": REFUND, "reason": "cancelled before the horse left the stable: premium refunded"}


def wants_cancel(task: dict[str, Any] | None) -> bool:
    """True once a cancellation that must stop the task has been recorded."""
    return bool(task and (task.get("cancellation") or {}).get("decision") == REFUND)


def log(task_id: str, kind: str, detail: dict[str, Any], ms: int | None = None) -> None:
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": task_id, "at": _now(), "kind": kind,
                                       "detail": detail, "duration_ms": ms, "system": detail.get("system")})


def _action(op_id: str) -> dict[str, Any] | None:
    return dbm.db()[dbm.ACTIONS].find_one({"operation_id": op_id})


def _settle(task_id: str, step: str, system: str, look_up: Callable) -> bool:
    """Is this earlier write proven? An intent that was never verified is read back (nothing is sent)."""
    rec = _action(operation_id(task_id, step))
    if not rec:
        return False
    if rec["state"] == Outcome.VERIFIED:
        return True
    rec = guarded_write(task_id=task_id, step=step, system=system, intended=rec["intended"],
                        guard=lambda: "cancelled", call=lambda *_: None, read_back=look_up(system),
                        max_attempts=0)
    return rec["state"] == Outcome.VERIFIED


def _write_once(task_id: str, step: str, system: str, intended: dict[str, Any], kind: str,
                send: Callable, look_up: Callable) -> dict[str, Any]:
    """One guarded write; log `kind` only the first time it is proven."""
    before = _action(operation_id(task_id, step))
    started = _now()
    rec = guarded_write(task_id=task_id, step=step, system=system, intended=intended,
                        guard=lambda: None, call=send(system), read_back=look_up(system))
    if rec["state"] == Outcome.VERIFIED and not (before and before["state"] == Outcome.VERIFIED):
        log(task_id, kind, {"system": system, "operation_id": rec["operation_id"], **intended},
            int((_now() - started).total_seconds() * 1000))
    return rec


def _summary(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    return {"operation_id": rec.get("operation_id"), "state": str(getattr(rec.get("state"), "value", rec.get("state"))),
            "attempts": rec.get("attempts")}


def settle(task_id: str, send: Callable | None = None, look_up: Callable | None = None) -> dict[str, Any]:
    """Finish a recorded cancellation. Safe to call any number of times, from anywhere."""
    if send is None or look_up is None:
        from .tools import look_up as _lu, send as _s
        send, look_up = send or _s, look_up or _lu
    tasks = dbm.db()[dbm.TASKS]
    task = tasks.find_one({"_id": task_id})
    c = task["cancellation"]
    out: dict[str, Any] = {"task_id": task_id, "decision": c["decision"], "reason": c["reason"],
                           "requested_at": c["requested_at"]}

    if c["decision"] == REFUSED:
        out.update(outcome="refused", refunded=False, policy_cancelled=False, status=task.get("status"))
        return out

    charged = _settle(task_id, "charge", "payments", look_up)
    issued = _settle(task_id, "issue", "platform", look_up)
    refund_rec = policy_rec = None
    pending = []
    if charged:
        charge = _action(operation_id(task_id, "charge"))["intended"]
        # Identical to the graph's refund-and-escalate intent: same op ID, same body.
        refund_rec = _write_once(task_id, "refund", "refunds",
                                 {"task_id": task_id, "refund_of": operation_id(task_id, "charge"),
                                  "amount": charge["amount"], "currency": charge["currency"]},
                                 "refund_verified", send, look_up)
        if refund_rec["state"] != Outcome.VERIFIED:
            pending.append("refund")
    if issued:
        issue = _action(operation_id(task_id, "issue"))["intended"]
        policy_rec = _write_once(task_id, "cancel_policy", "cancellations",
                                 {"task_id": task_id, "policy_of": operation_id(task_id, "issue"),
                                  "horse_id": issue.get("horse_id"), "race_id": issue.get("race_id")},
                                 "policy_cancelled", send, look_up)
        if policy_rec["state"] != Outcome.VERIFIED:
            pending.append("cancel_policy")

    status = "cancelling" if pending else "cancelled"
    fields: dict[str, Any] = {"status": status, "updated_at": _now()}
    if pending:
        fields["cancel_pending"] = pending
    else:
        fields["cancelled_at"] = task.get("cancelled_at") or _now()
    tasks.update_one({"_id": task_id}, {"$set": fields})
    if not pending and task.get("status") != "cancelled":
        log(task_id, "cancelled", {"system": "agent", "refunded": bool(charged), "policy_cancelled": bool(issued)})
    out.update(outcome="cancelled" if not pending else "pending", status=status, pending=pending,
               refunded=bool(refund_rec and refund_rec["state"] == Outcome.VERIFIED),
               policy_cancelled=bool(policy_rec and policy_rec["state"] == Outcome.VERIFIED),
               charged=charged, issued=issued, refund=_summary(refund_rec), policy_cancellation=_summary(policy_rec))
    return out


def request(task_id: str, now: datetime | None = None, **kw) -> dict[str, Any] | None:
    """The owner asks to cancel. Records the decision first (once), then settles. None if no such task."""
    now = now or _now()
    tasks = dbm.db()[dbm.TASKS]
    task = tasks.find_one({"_id": task_id})
    if not task:
        return None
    repeat = bool(task.get("cancellation"))
    if not repeat:
        c = {**decide(task, now), "requested_at": now, "status_at_request": task.get("status")}
        fields: dict[str, Any] = {"cancellation": c, "updated_at": now}
        if c["decision"] == REFUND:
            fields["status"] = "cancelling"  # not an OPEN status: the worker stops progressing it
        else:
            fields["cancel_refused_reason"] = c["reason"]
        # Atomic: only the first request writes the decision.
        won = tasks.update_one({"_id": task_id, "cancellation": None}, {"$set": fields}).modified_count
        repeat = not won
        if won:
            log(task_id, "cancel_requested", {"system": "agent", "decision": c["decision"],
                                              "status_at_request": c["status_at_request"]})
            if c["decision"] == REFUSED:
                log(task_id, "cancel_refused_after_departure", {"system": "agent", "reason": c["reason"]})
    out = settle(task_id, **kw)
    out["repeat"] = repeat
    return out
