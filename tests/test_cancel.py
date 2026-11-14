"""Cancellation: refund before the horse leaves, none after; never twice; finishes after a crash.

No network and no Atlas: a tiny in-memory stand-in for the few collection calls used.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from raceday import db as dbm
from raceday.agent import cancel
from raceday.ledger import Outcome

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


def _get(doc, key):
    for part in key.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def _match(doc, flt):
    for k, v in flt.items():
        held = _get(doc, k)
        if isinstance(v, dict) and "$ne" in v:
            if held == v["$ne"]:
                return False
        elif held != v:
            return False
    return True


class Coll:
    def __init__(self, unique=None):
        self.docs, self.unique = [], unique

    def find_one(self, flt=None, *_):
        return next((copy.deepcopy(d) for d in self.docs if _match(d, flt or {})), None)

    def insert_one(self, doc):
        if self.unique and any(d.get(self.unique) == doc.get(self.unique) for d in self.docs):
            raise DuplicateKeyError("dup")
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, flt, upd):
        for d in self.docs:
            if _match(d, flt):
                d.update(copy.deepcopy(upd.get("$set", {})))
                for k, n in upd.get("$inc", {}).items():
                    d[k] = d.get(k, 0) + n
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class Mocks:
    """The outside systems: one record per (system, operation ID), like mocks/app.py."""

    def __init__(self):
        self.ops, self.sends = {}, []

    def send(self, system):
        def _call(op_id, payload):
            self.sends.append((system, op_id))
            self.ops.setdefault((system, op_id), {"operation_id": op_id, "reference": "r", "payload": payload})
        return _call

    def look_up(self, system):
        return lambda op_id: self.ops.get((system, op_id))


@pytest.fixture
def world(monkeypatch):
    d = {dbm.TASKS: Coll(), dbm.ACTIONS: Coll(unique="operation_id"), dbm.TIMELINE: Coll()}
    monkeypatch.setattr(dbm, "db", lambda: d)
    mocks = Mocks()
    return d, mocks


def _covered_task(d, mocks, deadline):
    tid = "t1"
    d[dbm.TASKS].insert_one({"_id": tid, "status": "covered", "deadline": deadline, "horse_id": "h1"})
    for step, system, body in [("charge", "payments", {"task_id": tid, "amount": 120.5, "currency": "EUR"}),
                               ("issue", "platform", {"task_id": tid, "horse_id": "h1", "race_id": "r1"})]:
        op = f"{tid}:{step}"
        mocks.ops[(system, op)] = {"operation_id": op, "reference": "r", "payload": body}
        d[dbm.ACTIONS].insert_one({"operation_id": op, "task_id": tid, "step": step, "system": system,
                                   "intended": body, "state": Outcome.VERIFIED.value})
    return tid


def _run(tid, mocks, now=NOW):
    return cancel.request(tid, now=now, send=mocks.send, look_up=mocks.look_up)


def _kinds(d):
    return [e["kind"] for e in d[dbm.TIMELINE].docs]


def test_the_rule():
    assert cancel.decide({"deadline": NOW + timedelta(minutes=1)}, NOW)["decision"] == cancel.REFUND
    assert cancel.decide({"deadline": NOW}, NOW)["decision"] == cancel.REFUSED
    assert cancel.decide({"deadline": (NOW - timedelta(hours=1)).replace(tzinfo=None)}, NOW)["decision"] == cancel.REFUSED


def test_before_departure_refunds_and_cancels_the_policy(world):
    d, mocks = world
    tid = _covered_task(d, mocks, NOW + timedelta(hours=1))
    out = _run(tid, mocks)
    assert out["outcome"] == "cancelled" and out["refunded"] and out["policy_cancelled"]
    assert d[dbm.TASKS].find_one({"_id": tid})["status"] == "cancelled"
    assert mocks.ops[("refunds", f"{tid}:refund")]["payload"]["amount"] == 120.5
    assert ("cancellations", f"{tid}:cancel_policy") in mocks.ops
    assert ("platform", f"{tid}:cancel_policy") not in mocks.ops  # no second platform record
    assert _kinds(d) == ["cancel_requested", "refund_verified", "policy_cancelled", "cancelled"]


def test_cancelling_twice_refunds_once(world):
    d, mocks = world
    tid = _covered_task(d, mocks, NOW + timedelta(hours=1))
    _run(tid, mocks)
    again = _run(tid, mocks, now=NOW + timedelta(hours=2))  # even after departure: the decision stands
    assert again["repeat"] and again["outcome"] == "cancelled"
    assert [s for s in mocks.sends if s[0] == "refunds"] == [("refunds", f"{tid}:refund")]
    assert _kinds(d).count("refund_verified") == 1


def test_after_departure_is_refused_and_stays_covered(world):
    d, mocks = world
    tid = _covered_task(d, mocks, NOW - timedelta(minutes=5))
    out = _run(tid, mocks)
    assert out["outcome"] == "refused" and not out["refunded"]
    assert d[dbm.TASKS].find_one({"_id": tid})["status"] == "covered"
    assert not mocks.sends
    assert _kinds(d) == ["cancel_requested", "cancel_refused_after_departure"]


def test_nothing_charged_just_cancels(world):
    d, mocks = world
    d[dbm.TASKS].insert_one({"_id": "t2", "status": "awaiting_owner", "deadline": NOW + timedelta(hours=1)})
    out = _run("t2", mocks)
    assert out["outcome"] == "cancelled" and not out["refunded"] and not mocks.sends
    assert d[dbm.TASKS].find_one({"_id": "t2"})["status"] == "cancelled"


def test_crash_after_refund_intent_finishes_by_read_back(world):
    d, mocks = world
    tid = _covered_task(d, mocks, NOW + timedelta(hours=1))
    # Killed after the refund landed but before its read-back was recorded.
    d[dbm.TASKS].update_one({"_id": tid}, {"$set": {"status": "cancelling", "cancellation": {
        "decision": cancel.REFUND, "reason": "x", "requested_at": NOW}}})
    body = {"task_id": tid, "refund_of": f"{tid}:charge", "amount": 120.5, "currency": "EUR"}
    d[dbm.ACTIONS].insert_one({"operation_id": f"{tid}:refund", "task_id": tid, "step": "refund",
                               "system": "refunds", "intended": body, "state": "intent", "attempts": 1})
    mocks.ops[("refunds", f"{tid}:refund")] = {"operation_id": f"{tid}:refund", "reference": "r", "payload": body}
    out = _run(tid, mocks)
    assert out["refunded"] and out["status"] == "cancelled"
    assert not [s for s in mocks.sends if s[0] == "refunds"]  # proven by read-back, not re-sent


def test_unproven_charge_is_read_back_not_resent(world):
    d, mocks = world
    d[dbm.TASKS].insert_one({"_id": "t3", "status": "confirmed", "deadline": NOW + timedelta(hours=1)})
    d[dbm.ACTIONS].insert_one({"operation_id": "t3:charge", "task_id": "t3", "step": "charge", "system": "payments",
                               "intended": {"task_id": "t3", "amount": 9.0, "currency": "EUR"}, "state": "intent"})
    out = _run("t3", mocks)  # the charge never landed
    assert out["outcome"] == "cancelled" and not out["charged"] and not mocks.sends
