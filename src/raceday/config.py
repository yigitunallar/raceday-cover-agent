"""Settings, read from the environment. Secrets live in .env (never committed)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("DB_NAME", "raceday")
PORT = int(os.environ.get("PORT", "8000"))
# The mock outside systems are mounted on the API service under /mocks.
MOCKS_URL = os.environ.get("MOCKS_URL", f"http://127.0.0.1:{PORT}/mocks")
MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE = os.environ.get("ELEVENLABS_VOICE", "JBFqnCBsd6RMkjVDRZzb")  # a warm British male voice
# Real phone call (ElevenLabs agent over a Twilio number). See scripts/setup_phone_agent.py.
ELEVENLABS_AGENT_ID = os.environ.get("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_PHONE_NUMBER_ID = os.environ.get("ELEVENLABS_PHONE_NUMBER_ID", "")
VOICE_WEBHOOK_SECRET = os.environ.get("VOICE_WEBHOOK_SECRET", "")
DEMO_OWNER_PHONE = os.environ.get("DEMO_OWNER_PHONE", "")  # every invented owner rings this one real phone
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", "https://raceday-cover-agent.onrender.com")
