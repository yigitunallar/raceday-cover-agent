"""The API the web page talks to. It also hosts the mock systems under /mocks
and starts or kills the agent process, so a judge can pull the plug.

    uvicorn raceday.api.app:app --app-dir src --port 8000
"""
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config
from .. import db as dbm
from .. import seed as seed_module
from ..agent import voice
from ..mocks.app import app as mocks_app
from ..mocks.app import AFTER_WRITE as mocks_after_write
from ..mocks.app import summary as mock_summary
from .intake import router as intake_router

app = FastAPI(title="Raceday Cover Agent")
app.include_router(intake_router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/mocks", mocks_app)
app.mount("/ui", StaticFiles(directory=Path(__file__).resolve().parents[3] / "web"), name="ui")


@app.get("/", include_in_schema=False)
def ui_home():
    return RedirectResponse("/ui/mission.html")

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
    return {"ok": True, "agent_running": _running(), "scoped_outreach": True}


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


_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scoreboard")


@app.get("/scoreboard")
def scoreboard():
    systems = _POOL.submit(mock_summary)  # the two round trips run side by side
    counts: dict[str, int] = {}
    for row in dbm.db()[dbm.TASKS].aggregate([{"$match": {"quote_only": {"$ne": True}}},
                                              {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    declared = sum(counts.values())
    accounted = sum(counts.get(s, 0) for s in ("covered", "referred", "not_confirmed", "not_eligible", "cancelled"))
    return {"declared": declared, "covered": counts.get("covered", 0), "referred": counts.get("referred", 0),
            "not_confirmed": counts.get("not_confirmed", 0), "not_eligible": counts.get("not_eligible", 0),
            "cancelled": counts.get("cancelled", 0),
            "in_progress": declared - accounted, "by_status": counts, "systems": systems.result(timeout=10),
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
    _log_killed(None, "Agent killed: the plug was pulled")
    return {"ok": True, "was_running": True}


def _log_killed(task_id: str | None, message: str) -> None:
    try:
        dbm.db()[dbm.TIMELINE].insert_one({"task_id": task_id, "at": datetime.now(timezone.utc), "kind": "killed",
                                           "detail": {"system": "agent", "message": message}, "duration_ms": None})
    except Exception:  # noqa: BLE001 - logging never blocks a kill
        pass


@app.post("/control/kill-after-charge")
def control_kill_after_charge():
    """Arm a kill for the moment the next charge takes effect, before the agent hears back.

    The money has moved, the policy has not been issued, and the agent never learns
    whether its charge worked: the worst moment to crash, made repeatable for the demo.
    """
    dbm.db()[dbm.MOCK_CHAOS].update_one({"system": "kill_after:payments"},
                                        {"$set": {"mode": "kill", "count": 1}}, upsert=True)
    return {"ok": True, "armed": True, "agent_running": _running()}


def _kill_after_write(system: str, task_id: str | None = None) -> None:
    doc = dbm.db()[dbm.MOCK_CHAOS].find_one_and_update(
        {"system": f"kill_after:{system}", "count": {"$gt": 0}}, {"$inc": {"count": -1}})
    if doc and _running():
        _agent.send_signal(signal.SIGKILL)
        _agent.wait(timeout=5)
        _log_killed(task_id, "Agent killed right after the charge, before hearing back")


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
    health_notes: str | None = None
    health_resolved: bool = False


@app.post("/voice/answer")
def voice_answer(body: VoiceAnswer, x_webhook_secret: str = Header(default="")):
    """Called by the ElevenLabs phone agent's tool once the owner clearly says yes or no."""
    if not config.VOICE_WEBHOOK_SECRET or x_webhook_secret != config.VOICE_WEBHOOK_SECRET:
        raise HTTPException(401, "bad secret")
    if body.answer not in ("yes", "no"):
        raise HTTPException(400, "answer must be yes or no; an unclear answer is no answer")
    d = dbm.db()
    task = d[dbm.TASKS].find_one({"_id": body.task_id})
    if not task:
        raise HTTPException(404, "unknown task")
    notes = (body.health_notes or "").strip()
    if len(notes) > 2000:
        raise HTTPException(422, "health notes are too long")
    if task.get("voice_health_required") and body.answer == "yes" and not notes:
        raise HTTPException(409, "Ask for the missing health information before recording the answer")
    if (task.get("voice_health_required") or notes) and (not body.conversation_id or not d[dbm.TIMELINE].find_one(
            {"task_id": body.task_id, "kind": "owner_phoned", "detail.conversation_id": body.conversation_id})):
        raise HTTPException(409, "conversation does not match the recorded call")
    now = datetime.now(timezone.utc)
    fields = {"confirmation": {"fit": body.answer == "yes", "given_by": "owner (phone call)",
                               "conversation_id": body.conversation_id, "at": now}}
    if notes:
        fields.update(owner_health_followup={"notes": notes, "resolved": body.health_resolved,
                                              "conversation_id": body.conversation_id, "at": now},
                      investigation=None, quote=None)
    if task.get("voice_health_required") and body.answer == "yes" and not body.health_resolved:
        fields.pop("confirmation")
        fields.update(status="health_unresolved", reason="Owner could not resolve missing health information")
    result = d[dbm.TASKS].update_one(
        {"_id": body.task_id, "confirmation": None, "deadline": {"$gt": now}}, {"$set": fields})
    if result.matched_count:
        d[dbm.TIMELINE].insert_one({"task_id": body.task_id, "at": now, "kind": "health_followup_unresolved" if task.get("voice_health_required") and not body.health_resolved else "owner_answered",
                                   "detail": {"system": "voice", "answer": body.answer,
                                              "conversation_id": body.conversation_id,
                                              "health_followup_saved": bool(notes)}, "duration_ms": None})
    if not result.matched_count:
        return {"ok": False, "message": "Thanks. That answer could not be recorded: already answered or too late."}
    if task.get("voice_health_required") and body.answer == "yes" and not body.health_resolved:
        return {"ok": False, "message": "The health information is still incomplete, so the task is paused."}
    return {"ok": True, "message": "Thank you, the answer is recorded."}


# --- Demo controls: one call to the interesting starting state -------------------------

DEMO_UNSETTLED = ("new", "priced")  # not yet investigated and priced
DEMO_KEEP_DEFAULT = "Copper Lantern"


def _status_counts() -> dict[str, int]:
    return {row["_id"]: row["n"] for row in
            dbm.db()[dbm.TASKS].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}


def _demo_settled(counts: dict[str, int]) -> bool:
    """Every horse investigated and priced: none left at new or priced."""
    return bool(sum(counts.values())) and not any(counts.get(s) for s in DEMO_UNSETTLED)


def _awaiting_owner() -> list[dict]:
    rows = dbm.db()[dbm.TASKS].find({"status": "awaiting_owner"},
                                   {"horse": 1, "race.race_name": 1, "deadline": 1}).sort("deadline", 1)
    return _clean([{"task_id": t["_id"], "horse": t.get("horse"), "race": (t.get("race") or {}).get("race_name"),
                    "deadline": t.get("deadline")} for t in rows])


@app.post("/control/demo-start")
def control_demo_start(timeout: float = 150.0):
    """Kill, reset, seed, start the agent, and wait until every horse is investigated and priced.

    Owners are not confirmed, so horses stop at awaiting_owner (over-limit ones are referred,
    the past-deadline one is not_confirmed, the entry-stage one is not_eligible). The agent is
    left running, so a confirmation goes straight on to charge, issue and report.
    """
    from concurrent.futures import ThreadPoolExecutor, wait

    from ..agent.graph import build, run_task

    t0 = time.monotonic()
    control_kill()
    dbm.reset_all()
    seeded = seed_module.run()
    # Price every horse in parallel here, before the agent starts: the agent works one horse at
    # a time and would take minutes. No owner has answered, so no task gets past awaiting_owner:
    # nothing is charged here. run_task is the agent's own resumable step, checkpointed per task.
    graph = build()
    ids = [t["_id"] for t in dbm.db()[dbm.TASKS].find({"status": {"$in": list(DEMO_UNSETTLED)}}, {"_id": 1})]
    pool = ThreadPoolExecutor(max_workers=max(1, min(len(ids), 13)))
    futures = [pool.submit(run_task, graph, tid) for tid in ids]
    wait(futures, timeout=max(1.0, timeout - (time.monotonic() - t0)))
    pool.shutdown(wait=False, cancel_futures=True)
    errors = [repr(f.exception()) for f in futures if f.done() and f.exception()]
    control_start()  # the agent picks up anything left, and carries confirmed horses on to cover
    ready = False
    while time.monotonic() - t0 < timeout:
        if _demo_settled(_status_counts()):
            ready = True
            break
        if not _running():
            break
        time.sleep(1.0)
    out = {"ok": ready, "ready": ready, "seconds": round(time.monotonic() - t0, 1), "seeded": seeded,
           "by_status": _status_counts(), "awaiting_owner": _awaiting_owner(), "agent_running": _running(),
           "errors": errors}
    if not ready:
        out["message"] = "timed out, or the agent stopped, before every horse was priced"
    return out


@app.post("/control/confirm-all-but-one")
def control_confirm_all_but_one(keep: str = DEMO_KEEP_DEFAULT):
    """Confirm fit to run for every horse awaiting its owner, except `keep` (left for the live call).

    Same guard as /tasks/{id}/confirm: a horse already past its deadline, or already answered, is skipped.
    """
    now = datetime.now(timezone.utc)
    confirmed, skipped = [], []
    for t in dbm.db()[dbm.TASKS].find({"status": "awaiting_owner", "confirmation": None}).sort("deadline", 1):
        if t.get("horse") == keep:
            skipped.append({"task_id": t["_id"], "horse": t.get("horse"), "why": "kept for the live call"})
            continue
        result = dbm.db()[dbm.TASKS].update_one(
            {"_id": t["_id"], "confirmation": None, "deadline": {"$gt": now}},
            {"$set": {"confirmation": {"fit": True, "given_by": "owner (demo control)", "at": now}}})
        if result.matched_count:
            confirmed.append({"task_id": t["_id"], "horse": t.get("horse")})
        else:
            skipped.append({"task_id": t["_id"], "horse": t.get("horse"), "why": "past deadline or already answered"})
    return {"ok": True, "kept": keep, "confirmed": confirmed, "skipped": skipped, "agent_running": _running()}


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """The owner cancels. Before the horse leaves the stable: refund the premium and cancel the policy,
    each proven by read-back. After it has left: cover has started, no refund. Safe to repeat."""
    from ..agent import cancel as cancel_module
    out = cancel_module.request(task_id)
    if out is None:
        raise HTTPException(404, "unknown task")
    return _clean(out)


@app.post("/control/auto-call")
def control_auto_call(on: bool):
    """The switch for agent-initiated phone calls (off by default, so tests never ring the demo phone)."""
    from ..agent import outreach
    outreach.set_auto_calls(on)
    return {"ok": True, "auto_call": outreach.auto_calls_on()}


@app.get("/control/auto-call")
def control_auto_call_state():
    from ..agent import outreach
    return {"auto_call": outreach.auto_calls_on()}
