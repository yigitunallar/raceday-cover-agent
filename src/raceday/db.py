"""MongoDB access. Atlas is the record of what was intended and what was proven."""
import os
import time
from functools import lru_cache

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

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

TIMELINE = "timeline"  # recovery events per task, for the page

# `cases` is deliberately NOT in this list: a reset starts a new race day, but what
# the agent learned from past problems survives it. That is the point of the second,
# one-step fix. `system_profiles` is cleared, because reset puts the systems back.
OURS = [TASKS, ACTIONS, SYSTEM_PROFILES, HORSE_PROFILES, REFERRALS, RACES, INSTRUCTIONS, INSURER_RULES, TIMELINE]
MOCKS = [MOCK_OPS, MOCK_CHAOS]

CASES_VECTOR_INDEX = "cases_vector"
LOCAL_EMBED_DIMS = 256


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
    d[TIMELINE].create_index([("task_id", ASCENDING), ("at", ASCENDING)])
    ensure_vector_index(wait_seconds=0)


_vector_mode: str | None = None
_vector_ready = False


def vector_mode() -> str | None:
    """"auto" or "local": how cases are embedded. Creates the index if it is missing."""
    ensure_vector_index(wait_seconds=0)
    return _vector_mode


def ensure_vector_index(wait_seconds: float = 0) -> str | None:
    """Create the Atlas Vector Search index on `cases` if missing (idempotent).

    Preferred: Atlas automated embedding ("autoEmbed", voyage-4): Atlas embeds each
    case's `description` and the query text itself. Fallback, for clusters that
    reject autoEmbed: a plain vector index on `embedding`, filled with a
    deterministic hashed bag-of-words (agent/schema_recovery.local_embed).

    Returns "auto" or "local" once the index is queryable, or None if it is not
    queryable within wait_seconds.
    """
    global _vector_mode, _vector_ready
    if _vector_ready:
        return _vector_mode
    coll = db()[CASES]
    if CASES not in db().list_collection_names():
        try:
            db().create_collection(CASES)
        except CollectionInvalid:
            pass  # created by another process meanwhile
    found = [ix for ix in coll.list_search_indexes(CASES_VECTOR_INDEX)]
    if not found:
        filters = [{"type": "filter", "path": "system"}, {"type": "filter", "path": "failure_type"}]
        want_local = os.environ.get("EMBEDDING_MODE", "auto") == "local"
        try:
            if want_local:
                raise OperationFailure("EMBEDDING_MODE=local")
            coll.create_search_index(SearchIndexModel(name=CASES_VECTOR_INDEX, type="vectorSearch", definition={
                "fields": [{"type": "autoEmbed", "modality": "text", "path": "description", "model": "voyage-4"},
                           *filters]}))
        except OperationFailure:
            coll.create_search_index(SearchIndexModel(name=CASES_VECTOR_INDEX, type="vectorSearch", definition={
                "fields": [{"type": "vector", "path": "embedding", "numDimensions": LOCAL_EMBED_DIMS,
                            "similarity": "cosine"}, *filters]}))
    deadline = time.monotonic() + wait_seconds
    while True:
        ix = next(iter(coll.list_search_indexes(CASES_VECTOR_INDEX)), None)
        if ix:
            fields = (ix.get("latestDefinition") or {}).get("fields", [])
            _vector_mode = "auto" if any(f.get("type") == "autoEmbed" for f in fields) else "local"
            if ix.get("queryable"):
                _vector_ready = True
                return _vector_mode
        if time.monotonic() >= deadline:
            return None
        time.sleep(2)


def reset_all() -> None:
    """Clear our state and the mock systems together, so a demo always starts clean."""
    d = db()
    for name in OURS + MOCKS + ["checkpoints", "checkpoint_writes"]:
        d[name].delete_many({})
    ensure_indexes()
