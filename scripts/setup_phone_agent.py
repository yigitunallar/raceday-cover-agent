"""One-time setup for the real phone call. Prints IDs to put in .env and on Render.

    python scripts/setup_phone_agent.py agent    # create the answer tool and the phone agent
    python scripts/setup_phone_agent.py number   # register the Twilio number with ElevenLabs

Needs ELEVENLABS_API_KEY, VOICE_WEBHOOK_SECRET, and for "number" the TWILIO_* values.
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
KEY = os.environ["ELEVENLABS_API_KEY"]
API = "https://api.elevenlabs.io/v1/convai"
H = {"xi-api-key": KEY}
PUBLIC = os.environ.get("PUBLIC_API_URL", "https://raceday-cover-agent.onrender.com")
VOICE = os.environ.get("ELEVENLABS_VOICE", "JBFqnCBsd6RMkjVDRZzb")

PROMPT = """You are the raceday cover agent, phoning a racehorse owner. Your only job on this call is to get a clear
yes or no to one question: is {{horse}} fit to run today in the {{race}}?

Facts you may mention if asked (do not invent others): cover is {{cover}}; the price is {{price}};
the price reflects {{reasons}}; exclusions: {{exclusions}}.

Rules:
- Be brief, warm and professional. This is a short call.
- Only a clear yes or a clear no counts. If the answer is unclear ("I think so", "probably"), ask once more.
  If it is still unclear, say you will record no answer, and end the call politely without calling the tool.
- As soon as you have a clear answer, call record_answer exactly once with "yes" or "no", read back what you
  recorded, thank the owner and end the call.
- Never answer for the owner, never change the price, and never promise cover: cover is confirmed separately."""

FIRST = ("Hello, this is your raceday cover agent calling about {{horse}} in the {{race}} today. "
         "Before the horse leaves the yard: is {{horse}} fit to run today?")


def agent() -> None:
    secret = os.environ["VOICE_WEBHOOK_SECRET"]
    tool = httpx.post(f"{API}/tools", headers=H, timeout=30, json={"tool_config": {
        "type": "webhook", "name": "record_answer",
        "description": "Record the owner's clear yes or no to 'is the horse fit to run today?'. Call once, only for a clear answer.",
        "execution_mode": "immediate", "response_timeout_secs": 20,
        "api_schema": {
            "url": f"{PUBLIC}/voice/answer", "method": "POST", "content_type": "application/json",
            "request_headers": {"X-Webhook-Secret": secret},
            "request_body_schema": {"type": "object", "required": ["task_id", "answer"], "properties": {
                "task_id": {"type": "string", "dynamic_variable": "task_id"},
                "conversation_id": {"type": "string", "dynamic_variable": "system__conversation_id"},
                "answer": {"type": "string", "enum": ["yes", "no"],
                           "description": "yes if the owner clearly said the horse is fit to run, no if clearly not"}}}}}})
    print("tool:", tool.status_code, tool.text[:300])
    tool.raise_for_status()
    tool_id = tool.json()["id"]
    r = httpx.post(f"{API}/agents/create", headers=H, timeout=30, json={
        "name": "Raceday cover agent: owner call",
        "conversation_config": {
            "agent": {"first_message": FIRST, "language": "en",
                      "prompt": {"prompt": PROMPT, "llm": "claude-haiku-5-5", "tool_ids": [tool_id]},
                      "dynamic_variables": {"dynamic_variable_placeholders": {
                          "task_id": "test", "horse": "Copper Lantern", "race": "Kilbarrow Handicap",
                          "cover": "60,000 euros", "price": "378.00 euros including tax",
                          "reasons": "no extra risk factors", "exclusions": "none"}}},
            "tts": {"voice_id": VOICE}}})
    print("agent:", r.status_code, r.text[:300])
    r.raise_for_status()
    print(f"\nELEVENLABS_AGENT_ID={r.json()['agent_id']}")


def number() -> None:
    agent_id = os.environ["ELEVENLABS_AGENT_ID"]
    r = httpx.post(f"{API}/phone-numbers", headers=H, timeout=30, json={
        "phone_number": os.environ["TWILIO_PHONE_NUMBER"], "label": "Raceday cover agent",
        "sid": os.environ["TWILIO_ACCOUNT_SID"], "token": os.environ["TWILIO_AUTH_TOKEN"],
        "provider": "twilio", "agent_id": agent_id})
    print("number:", r.status_code, r.text[:300])
    r.raise_for_status()
    print(f"\nELEVENLABS_PHONE_NUMBER_ID={r.json()['phone_number_id']}")


if __name__ == "__main__":
    {"agent": agent, "number": number}[sys.argv[1]]()
