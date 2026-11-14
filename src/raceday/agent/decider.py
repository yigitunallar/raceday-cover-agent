"""When a write's outcome is not proven, the model decides the next move. Code decides if it is allowed.

The ledger has already read back and re-sent a few times with the same operation
ID. If the action is still not verified, the model looks at the evidence and picks
one move from a short menu. Code checks the move against the rules before anything
happens, and every decision and its reason goes to the timeline.
"""
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .. import db as dbm
from . import writer

MOVES = {
    "read_back_again": "Ask the target system again whether it holds this operation. Sends nothing.",
    "resend_same_id": "Send the same request again with the same operation ID (the system ignores repeats).",
    "refund_and_escalate": "Refund the charge (if one was taken) and hand the case to a human.",
    "escalate": "Stop and hand the case to a human, with the evidence. No money moves.",
}

SYSTEM = (
    "You are the recovery decision-maker for an insurance agent that buys raceday cover for horses. "
    "A step's outcome is not proven after the ledger read back and re-sent with the same operation ID. "
    "Choose exactly one move from the menu. Prefer gathering evidence before acting; prefer a re-send with the "
    "same ID over giving up while there is time before the horse leaves the stable; if money was taken and cover "
    "cannot be completed before the horse leaves, refund. Never invent facts. "
    'Reply with JSON only: {"move": "<one of the menu keys>", "reason": "<one short sentence>"}'
)


def allowed_moves(facts: dict[str, Any]) -> list[str]:
    """The rules, in code. The model can only choose among these."""
    moves = ["read_back_again", "escalate"]
    if not facts["horse_has_left"]:
        moves.append("resend_same_id")  # a repeat with the same ID can never double-charge
    if facts["charge_verified"]:
        moves.append("refund_and_escalate")
    if facts["decisions_so_far"] >= 3:
        moves = [m for m in moves if m in ("escalate", "refund_and_escalate")]  # stop looping
    return moves


def _template(facts: dict[str, Any]) -> dict[str, str]:
    """If the model is unavailable: the safe default."""
    allowed = facts["allowed_moves"]
    if "resend_same_id" in allowed and facts["decisions_so_far"] < 2:
        return {"move": "resend_same_id", "reason": "Outcome unknown and time remains: re-send with the same ID."}
    if "refund_and_escalate" in allowed:
        return {"move": "refund_and_escalate", "reason": "Cover could not be completed: refund and hand to a human."}
    return {"move": "escalate", "reason": "Outcome still unknown: hand to a human with the evidence."}


def decide(facts: dict[str, Any], call: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """Returns {move, reason, source, ms}. Never raises; an invalid choice falls back to the safe default."""
    started = datetime.now(timezone.utc)
    call = call or writer._call_model
    try:
        text = call(SYSTEM, {**facts, "menu": {k: MOVES[k] for k in facts["allowed_moves"]}})
        m = re.search(r"\{.*\}", text, re.S)
        choice = json.loads(m.group(0)) if m else {}
        if choice.get("move") not in facts["allowed_moves"]:
            raise ValueError(f"move not allowed: {choice.get('move')!r}")
        out = {"move": choice["move"], "reason": str(choice.get("reason", ""))[:240], "source": "model"}
    except Exception as e:  # noqa: BLE001 - a dead model must never strand a horse
        out = {**_template(facts), "source": "template", "fallback_reason": repr(e)[:200]}
    out["ms"] = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return out


def log(task_id: str, kind: str, detail: dict[str, Any], ms: int | None = None) -> None:
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": task_id, "at": datetime.now(timezone.utc), "kind": kind,
                                       "detail": detail, "duration_ms": ms, "system": detail.get("system")})
