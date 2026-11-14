"""The agent's one human moment, by voice: it calls the owner for the fit-to-run answer.

ElevenLabs turns the agent's question into speech. The owner's answer still goes
through the normal confirm endpoint, so code records who answered and when; the
agent never answers for the owner.
"""
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import config
from .. import db as dbm


def call_script(task: dict[str, Any]) -> str:
    q = task.get("quote") or {}
    race = task.get("race") or {}
    name = task.get("horse", "your horse")
    reasons = [f["evidence"] for f in q.get("risk_factors", [])]
    lines = [f"Hello, this is your raceday cover agent. {name} runs in the {race.get('race_name', 'race')} today."]
    if q.get("offerable"):
        lines.append(f"Cover is {q['sum_insured']:,.0f} euros, for {q['total']:,.2f} euros including tax.")
        if reasons:
            lines.append("The price reflects " + " and ".join(reasons[:2]) + ".")
        for x in q.get("exclusions", []):
            lines.append(f"As a pre-existing condition, the {x} is excluded.")
    lines.append(f"Before {name} leaves the yard: is {name} fit to run today? Please answer yes or no.")
    return " ".join(lines)


def speak(text: str) -> bytes:
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE}",
                   params={"output_format": "mp3_44100_128"},
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
                   json={"text": text, "model_id": "eleven_flash_v2_5"}, timeout=30)
    r.raise_for_status()
    return r.content


def log_call(task_id: str, text: str) -> None:
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": task_id, "at": datetime.now(timezone.utc), "kind": "owner_called",
                                       "detail": {"system": "voice", "script": text}, "duration_ms": None})


def phone_variables(task: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the phone agent is told about this horse. Everything comes from code-computed facts."""
    q = task.get("quote") or {}
    history = (task.get("owner_medical_history") or "").lower()
    required = not task.get("owner_health_followup") and (not (profile or {}).get("health_records") or
        any(word in history for word in ("unknown", "unclear", "incomplete")))
    return {
        "missing_information": str((task.get("investigation") or {}).get("summary") or "No investigation summary available.")[:1000],
        "health_followup_required": "yes" if required else "no",
        "health_question": ("Please describe the horse's health history, recent vet examination, any known conditions, and follow-up still needed." if required else "No additional health question is required."),
        "task_id": task["_id"], "horse": task.get("horse", "your horse"),
        "race": (task.get("race") or {}).get("race_name", "the race"),
        "cover": f"{q.get('sum_insured', 0):,.0f} euros" if q.get("offerable") else "not yet priced",
        "price": f"{q.get('total', 0):,.2f} euros including tax" if q.get("offerable") else "not yet priced",
        "reasons": " and ".join(f["evidence"] for f in q.get("risk_factors", [])[:2]) or "no extra risk factors",
        "exclusions": ", ".join(q.get("exclusions", [])) or "none",
    }


def place_call(task: dict[str, Any]) -> dict[str, Any]:
    """Ring the owner (in the demo: one real teammate phone) through ElevenLabs and Twilio."""
    missing = [n for n, v in [("ELEVENLABS_AGENT_ID", config.ELEVENLABS_AGENT_ID),
                              ("ELEVENLABS_PHONE_NUMBER_ID", config.ELEVENLABS_PHONE_NUMBER_ID),
                              ("DEMO_OWNER_PHONE", config.DEMO_OWNER_PHONE)] if not v]
    if missing:
        raise RuntimeError(f"phone calling not configured: {missing}")
    d = dbm.db()
    profile = d[dbm.HORSE_PROFILES].find_one({"horse_id": task.get("horse_id")}) or {}
    variables = phone_variables(task, profile)
    d[dbm.TASKS].update_one({"_id": task["_id"]}, {"$set": {"voice_health_required": variables["health_followup_required"] == "yes"}})
    r = httpx.post("https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                   json={"agent_id": config.ELEVENLABS_AGENT_ID,
                         "agent_phone_number_id": config.ELEVENLABS_PHONE_NUMBER_ID,
                         "to_number": config.DEMO_OWNER_PHONE,
                         "conversation_initiation_client_data": {"dynamic_variables": variables}},
                   timeout=30)
    r.raise_for_status()
    out = r.json()
    dbm.db()[dbm.TIMELINE].insert_one({"task_id": task["_id"], "at": datetime.now(timezone.utc), "kind": "owner_phoned",
                                       "detail": {"system": "voice", "conversation_id": out.get("conversation_id")},
                                       "duration_ms": None})
    return out
