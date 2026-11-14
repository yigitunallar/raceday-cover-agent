"""While a horse waits for its owner's fit-to-run answer, the model decides how to reach them.

Three real options: call now, send an in-app reminder, or wait. The owner's own
preferences and the time left are what should decide it, so the right answer
differs per owner, and the model must say why. Code then limits the call option:
only while the switch is on, at most two calls per horse, never in the owner's
quiet hours, never after the horse leaves, and one call at a time with a gap
between calls (there is one demo phone). A refused call is logged with the reason.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .. import db as dbm
from . import voice, writer

MOVES = ("call_now", "app_reminder", "wait")
DECIDE_EVERY = timedelta(seconds=75)  # how often a waiting horse is reconsidered
CALL_GAP = timedelta(minutes=3)  # one demo phone: space calls out
MAX_CALLS, MAX_REMINDERS = 2, 2
TZ = ZoneInfo("America/Toronto")
SETTINGS = "settings"

SYSTEM = (
    "You are the outreach planner of an insurance agent that needs one owner's fit-to-run answer before their horse "
    "leaves the yard. Choose exactly one move: call_now (a phone call: intrusive, use it when the owner prefers calls "
    "or time is short), app_reminder (a gentle in-app nudge: best for owners who answer in the app), or wait (when it "
    "is too early, a contact was made very recently, or the owner asked not to be called now). Respect the owner's "
    "stated preferences. Calling is not the default. "
    'Reply with JSON only: {"move": "call_now|app_reminder|wait", "reason": "<one short sentence>"}'
)


def auto_calls_on() -> bool:
    doc = dbm.db()[SETTINGS].find_one({"_id": "outreach"}) or {}
    return bool(doc.get("auto_call"))


def set_auto_calls(on: bool) -> None:
    dbm.db()[SETTINGS].update_one({"_id": "outreach"}, {"$set": {"auto_call": bool(on)}}, upsert=True)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def facts(task: dict[str, Any], now: datetime) -> dict[str, Any]:
    contact = task.get("owner_contact") or {}
    history = task.get("outreach") or []
    last = max((_aware(h["at"]) for h in history if h.get("move") in ("call_now", "app_reminder") and h.get("done")),
               default=None)
    return {
        "horse": task.get("horse"),
        "minutes_until_horse_leaves": int((_aware(task["deadline"]) - now).total_seconds() // 60),
        "local_time_toronto": now.astimezone(TZ).strftime("%H:%M"),
        "owner_prefers": contact.get("prefers"), "owner_notes": contact.get("notes"),
        "owner_quiet_until": contact.get("quiet_until"),
        "calls_so_far": sum(1 for h in history if h.get("move") == "call_now" and h.get("done")),
        "reminders_so_far": sum(1 for h in history if h.get("move") == "app_reminder" and h.get("done")),
        "minutes_since_last_contact": None if last is None else int((now - last).total_seconds() // 60),
    }


def call_refusal(f: dict[str, Any], now: datetime) -> str | None:
    """The rules for a phone call, in code. None means allowed."""
    if not auto_calls_on():
        return "agent calling is switched off"
    if f["minutes_until_horse_leaves"] <= 5:
        return "the horse is about to leave"
    if f["calls_so_far"] >= MAX_CALLS:
        return "already called this owner twice for this horse"
    q = f.get("owner_quiet_until")
    if q and now.astimezone(TZ).strftime("%H:%M") < q:
        return f"owner's quiet hours until {q}"
    recent = dbm.db()[dbm.TIMELINE].find_one({"kind": "owner_phoned", "at": {"$gt": now - CALL_GAP}})
    if recent:
        return "another call was placed less than 3 minutes ago (one demo phone)"
    return None


def decide(f: dict[str, Any], call: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        text = (call or writer._call_model)(SYSTEM, f)
        m = re.search(r"\{.*\}", text, re.S)
        choice = json.loads(m.group(0)) if m else {}
        if choice.get("move") not in MOVES:
            raise ValueError(f"unknown move {choice.get('move')!r}")
        out = {"move": choice["move"], "reason": str(choice.get("reason", ""))[:240], "source": "model"}
    except Exception as e:  # noqa: BLE001 - outreach must never block a horse
        urgent = f["minutes_until_horse_leaves"] < 60
        out = {"move": "call_now" if urgent and f["owner_prefers"] == "phone" else
               ("app_reminder" if f["reminders_so_far"] < 1 else "wait"),
               "reason": "fallback rule", "source": "template", "fallback_reason": repr(e)[:160]}
    out["ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return out


def maybe_reach_owner(task: dict[str, Any], call=None) -> dict[str, Any] | None:
    """Called while a horse waits for its owner. Decides at most every DECIDE_EVERY, only while the switch is on."""
    settings = dbm.db()[SETTINGS].find_one({"_id": "outreach"}) or {}
    if not settings.get("auto_call") or task.get("confirmation"):
        return None
    if settings.get("only_task_id") and settings["only_task_id"] != task.get("_id"):
        return None
    now = datetime.now(timezone.utc)
    history = task.get("outreach") or []
    if history and now - _aware(history[-1]["at"]) < DECIDE_EVERY:
        return None
    f = facts(task, now)
    choice = decide(f, call)
    entry = {"at": now, "move": choice["move"], "reason": choice["reason"], "source": choice["source"], "done": False}
    if choice["move"] == "call_now":
        refusal = call_refusal(f, now)
        if refusal:
            entry.update(refused=refusal)
        else:
            try:
                voice.place_call(task)
                entry["done"] = True
            except Exception as e:  # noqa: BLE001
                entry["error"] = repr(e)[:160]
    elif choice["move"] == "app_reminder":
        if f["reminders_so_far"] >= MAX_REMINDERS:
            entry.update(refused="already sent two reminders")
        else:
            entry["done"] = True  # simulated in-app notification
    dbm.db()[dbm.TASKS].update_one({"_id": task["_id"]}, {"$push": {"outreach": entry}})
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": task["_id"], "at": now, "kind": "outreach_decision", "duration_ms": choice["ms"],
                                       "detail": {"system": "outreach", "move": choice["move"], "reason": choice["reason"],
                                                  "source": choice["source"], "refused": entry.get("refused"),
                                                  "done": entry["done"]}})
    return entry
