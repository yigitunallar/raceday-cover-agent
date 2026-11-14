"""Mock outside systems: payment processor, our policy platform, insurer, notifications.

Contract (the same for every system):
- every write takes an operation ID and is safe to repeat with the same ID
- every system can answer "does operation X exist, and with what result?"
- chaos controls: drop the response, reject, rename a field
State lives in Atlas so a restart of this service loses nothing.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from .. import db as dbm

SYSTEMS = {"payments", "platform", "insurer", "notify"}
MODES = {"none", "drop_response", "reject", "rename_field"}

app = FastAPI(title="Mock outside systems")


class Write(BaseModel):
    operation_id: str
    payload: dict[str, Any]


class Chaos(BaseModel):
    system: str
    mode: str = "none"
    count: int = 1  # how many requests it affects


def _take_chaos(system: str) -> str:
    """Use up one unit of chaos for this system, if any is armed."""
    doc = dbm.db()[dbm.MOCK_CHAOS].find_one_and_update(
        {"system": system, "count": {"$gt": 0}}, {"$inc": {"count": -1}})
    return doc["mode"] if doc else "none"


def _check(system: str) -> None:
    if system not in SYSTEMS:
        raise HTTPException(404, f"unknown system {system}")


@app.post("/{system}/operations")
def write(system: str, body: Write):
    _check(system)
    mode = _take_chaos(system)
    if mode == "reject":
        raise HTTPException(422, "rejected by the system")
    ops = dbm.db()[dbm.MOCK_OPS]
    doc = {"system": system, "operation_id": body.operation_id, "payload": body.payload,
           "reference": f"{system[:3]}_{abs(hash(body.operation_id)) % 10**8:08d}",
           "created_at": datetime.now(timezone.utc)}
    try:
        ops.insert_one(doc)
    except DuplicateKeyError:
        doc = ops.find_one({"system": system, "operation_id": body.operation_id})  # same ID: no second effect
    if mode == "drop_response":
        # The write happened, but the caller never hears about it.
        raise HTTPException(504, "gateway timeout")
    return _public(doc, renamed=(mode == "rename_field"))


@app.get("/{system}/operations/{operation_id}")
def read(system: str, operation_id: str):
    _check(system)
    doc = dbm.db()[dbm.MOCK_OPS].find_one({"system": system, "operation_id": operation_id})
    if not doc:
        raise HTTPException(404, "no such operation")
    return _public(doc, renamed=(_take_chaos(f"{system}:read") == "rename_field"))


@app.post("/chaos")
def chaos(body: Chaos):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    dbm.db()[dbm.MOCK_CHAOS].update_one(
        {"system": body.system}, {"$set": {"mode": body.mode, "count": body.count}}, upsert=True)
    return {"armed": body.model_dump()}


@app.get("/summary")
def summary():
    """Counts per system, and any task that ended up with more than one effect."""
    ops = dbm.db()[dbm.MOCK_OPS]
    out: dict[str, Any] = {}
    for system in sorted(SYSTEMS):
        rows = list(ops.aggregate([
            {"$match": {"system": system}},
            {"$group": {"_id": "$payload.task_id", "n": {"$sum": 1}}},
        ]))
        out[system] = {"records": sum(r["n"] for r in rows), "duplicates": sum(r["n"] - 1 for r in rows if r["n"] > 1)}
    return out


def _public(doc: dict[str, Any], renamed: bool = False) -> dict[str, Any]:
    out = {"operation_id": doc["operation_id"], "reference": doc["reference"], "payload": doc["payload"]}
    if renamed:
        # "The system changed its fields": same data, different names.
        out = {"op_id": out["operation_id"], "ref": out["reference"], "data": out["payload"]}
    return out
