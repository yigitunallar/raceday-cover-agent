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
