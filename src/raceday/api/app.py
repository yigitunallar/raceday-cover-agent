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

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config
from .. import db as dbm
from .. import seed as seed_module
from ..agent import voice
from ..mocks.app import app as mocks_app
from ..mocks.app import AFTER_WRITE as mocks_after_write
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
        {"_id": task_id, "confirmation": None, "deadline": {"$gt": datetime.now(timezone.utc)}},
        {"$set": {"confirmation": {"fit": body.fit, "given_by": body.given_by, "at": datetime.now(timezone.utc)}}})
    if not result.matched_count:
        raise HTTPException(409, "unknown task, already answered, or the horse has left the stable")
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


@app.post("/control/kill-after-charge")
def control_kill_after_charge():
    """Arm a kill for the moment the next charge takes effect, before the agent hears back.

    The money has moved, the policy has not been issued, and the agent never learns
    whether its charge worked: the worst moment to crash, made repeatable for the demo.
    """
    dbm.db()[dbm.MOCK_CHAOS].update_one({"system": "kill_after:payments"},
                                        {"$set": {"mode": "kill", "count": 1}}, upsert=True)
    return {"ok": True, "armed": True, "agent_running": _running()}


def _kill_after_write(system: str) -> None:
    doc = dbm.db()[dbm.MOCK_CHAOS].find_one_and_update(
        {"system": f"kill_after:{system}", "count": {"$gt": 0}}, {"$inc": {"count": -1}})
    if doc and _running():
        _agent.send_signal(signal.SIGKILL)
        _agent.wait(timeout=5)


mocks_after_write.append(_kill_after_write)


@app.get("/timeline")
def timeline(task_id: str | None = None, limit: int = 200):
    """Recovery events (schema change detected, case found, model mapping, mapping validated)."""
    query = {"task_id": task_id} if task_id else {}
    rows = dbm.db()[dbm.TIMELINE].find(query, {"_id": 0}).sort("at", 1).limit(min(limit, 1000))
    return _clean(list(rows))


@app.post("/tasks/{task_id}/call")
def call_owner(task_id: str):
    """The agent phones the owner: returns the spoken question as audio (ElevenLabs)."""
    task = dbm.db()[dbm.TASKS].find_one({"_id": task_id})
    if not task:
        raise HTTPException(404, "unknown task")
    text = voice.call_script(task)
    try:
        audio = voice.speak(text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"voice unavailable: {e}") from e
    voice.log_call(task_id, text)
    return Response(content=audio, media_type="audio/mpeg", headers={"X-Call-Script": text.encode("ascii", "ignore").decode()})


@app.post("/tasks/{task_id}/phone")
def phone_owner(task_id: str):
    """Ring the owner for real (ElevenLabs agent over Twilio). Their spoken answer arrives at /voice/answer."""
    task = dbm.db()[dbm.TASKS].find_one({"_id": task_id})
    if not task:
        raise HTTPException(404, "unknown task")
    if task.get("confirmation"):
        raise HTTPException(409, "the owner has already answered")
    try:
        out = voice.place_call(task)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"call failed: {e}") from e
    return {"ok": True, "conversation_id": out.get("conversation_id"), "call_sid": out.get("callSid")}


class VoiceAnswer(BaseModel):
    task_id: str
    answer: str  # "yes" or "no", as the phone agent heard it
    conversation_id: str | None = None


@app.post("/voice/answer")
def voice_answer(body: VoiceAnswer, x_webhook_secret: str = Header(default="")):
    """Called by the ElevenLabs phone agent's tool once the owner clearly says yes or no."""
    if not config.VOICE_WEBHOOK_SECRET or x_webhook_secret != config.VOICE_WEBHOOK_SECRET:
        raise HTTPException(401, "bad secret")
    if body.answer not in ("yes", "no"):
        raise HTTPException(400, "answer must be yes or no; an unclear answer is no answer")
    result = dbm.db()[dbm.TASKS].update_one(
        {"_id": body.task_id, "confirmation": None, "deadline": {"$gt": datetime.now(timezone.utc)}},
        {"$set": {"confirmation": {"fit": body.answer == "yes", "given_by": "owner (phone call)",
                                   "conversation_id": body.conversation_id, "at": datetime.now(timezone.utc)}}})
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": body.task_id, "at": datetime.now(timezone.utc), "kind": "owner_answered",
                                       "detail": {"system": "voice", "answer": body.answer,
                                                  "conversation_id": body.conversation_id}, "duration_ms": None})
    if not result.matched_count:
        return {"ok": False, "message": "Thanks. That answer could not be recorded: already answered or too late."}
    return {"ok": True, "message": "Thank you, the answer is recorded."}

