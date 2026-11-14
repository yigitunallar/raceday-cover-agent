"""MongoDB access. Atlas is the record of what was intended and what was proven."""
from functools import lru_cache

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from . import config

# Our own collections.
TASKS = "tasks"  # one horse in one race
ACTIONS = "actions"  # the ledger: one attempt to change an outside system
CASES = "cases"  # solved problems, for Vector Search
SYSTEM_PROFILES = "system_profiles"  # what we believe about each system's fields
HORSE_PROFILES = "horse_profiles"  # risk profile per horse
REFERRALS = "referrals"  # cases sent to an underwriter
RACES = "races"
INSTRUCTIONS = "instructions"  # owners' standing instructions
INSURER_RULES = "insurer_rules"  # versioned, every value invented
# State of the mock outside systems (kept apart so "reset" is easy to reason about).
MOCK_OPS = "mock_operations"
MOCK_CHAOS = "mock_chaos"

OURS = [TASKS, ACTIONS, CASES, SYSTEM_PROFILES, HORSE_PROFILES, REFERRALS, RACES, INSTRUCTIONS, INSURER_RULES]
MOCKS = [MOCK_OPS, MOCK_CHAOS]


@lru_cache
def client() -> MongoClient:
    if not config.MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not set. Copy .env.example to .env and fill it in.")
    return MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000, appname="raceday-cover-agent")


def db() -> Database:
    return client()[config.DB_NAME]


def ensure_indexes() -> None:
    d = db()
    # One ledger record per operation ID: this is what makes a re-send safe.
    d[ACTIONS].create_index([("operation_id", ASCENDING)], unique=True)
    d[ACTIONS].create_index([("task_id", ASCENDING), ("step", ASCENDING)])
    d[TASKS].create_index([("status", ASCENDING), ("deadline", ASCENDING)])
    d[HORSE_PROFILES].create_index([("horse_id", ASCENDING)], unique=True)
    d[MOCK_OPS].create_index([("system", ASCENDING), ("operation_id", ASCENDING)], unique=True)


def reset_all() -> None:
    """Clear our state and the mock systems together, so a demo always starts clean."""
    d = db()
    for name in OURS + MOCKS + ["checkpoints", "checkpoint_writes"]:
        d[name].delete_many({})
    ensure_indexes()
