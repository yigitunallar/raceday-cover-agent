"""The API the web page talks to. It also hosts the mock systems under /mocks
and starts or kills the agent process, so a judge can pull the plug.

    uvicorn raceday.api.app:app --app-dir src --port 8000
"""
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import db as dbm
from .. import seed as seed_module
from ..mocks.app import app as mocks_app
from ..mocks.app import summary as mock_summary

app = FastAPI(title="Raceday Cover Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/mocks", mocks_app)

SRC = str(Path(__file__).resolve().parents[2])
_agent: subprocess.Popen | None = None


class Confirmation(BaseModel):
    fit: bool
    given_by: str = "owner"


def _clean(doc: Any) -> Any:
    if isinstance(doc, dict):
        return {k: _clean(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_clean(v) for v in doc]
    if isinstance(doc, datetime):
        return (doc if doc.tzinfo else doc.replace(tzinfo=timezone.utc)).isoformat()
    return doc


def _running() -> bool:
    return _agent is not None and _agent.poll() is None


@app.on_event("startup")
def startup() -> None:
    dbm.ensure_indexes()


@app.get("/health")
def health():
    dbm.client().admin.command("ping")
    return {"ok": True, "agent_running": _running()}


@app.get("/tasks")
def tasks():
    return _clean(list(dbm.db()[dbm.TASKS].find().sort("deadline", 1)))


@app.get("/tasks/{task_id}/actions")
def actions(task_id: str):
    return _clean(list(dbm.db()[dbm.ACTIONS].find({"task_id": task_id}, {"_id": 0}).sort("created_at", 1)))


@app.get("/referrals")
def referrals():
    return _clean(list(dbm.db()[dbm.REFERRALS].find()))


@app.post("/tasks/{task_id}/confirm")
def confirm(task_id: str, body: Confirmation):
    """The owner's single fit-to-run answer for this race."""
    result = dbm.db()[dbm.TASKS].update_one(
        {"_id": task_id, "confirmation": None},
        {"$set": {"confirmation": {"fit": body.fit, "given_by": body.given_by, "at": datetime.now(timezone.utc)}}})
    if not result.matched_count:
        raise HTTPException(409, "unknown task, or already answered")
    return {"ok": True}


@app.get("/scoreboard")
def scoreboard():
    counts: dict[str, int] = {}
    for row in dbm.db()[dbm.TASKS].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    declared = sum(counts.values())
    accounted = sum(counts.get(s, 0) for s in ("covered", "referred", "not_confirmed", "not_eligible"))
    return {"declared": declared, "covered": counts.get("covered", 0), "referred": counts.get("referred", 0),
            "not_confirmed": counts.get("not_confirmed", 0), "not_eligible": counts.get("not_eligible", 0),
            "in_progress": declared - accounted, "by_status": counts, "systems": mock_summary(),
            "agent_running": _running()}


@app.post("/control/seed")
def control_seed():
    return seed_module.run()


@app.post("/control/reset")
def control_reset():
    dbm.reset_all()
    return {"ok": True}


@app.post("/control/start")
def control_start():
    global _agent
    if _running():
        return {"ok": True, "already_running": True}
    env = {**os.environ, "PYTHONPATH": SRC}
    _agent = subprocess.Popen([sys.executable, "-m", "raceday.worker"], env=env, cwd=SRC)
    return {"ok": True, "pid": _agent.pid}


@app.post("/control/kill")
def control_kill():
    """Pull the plug: no warning, no clean-up."""
    if not _running():
        return {"ok": True, "was_running": False}
    _agent.send_signal(signal.SIGKILL)
    _agent.wait(timeout=5)
    return {"ok": True, "was_running": True}
