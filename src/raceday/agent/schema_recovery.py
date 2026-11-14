"""When an outside system changes its field names: recover, learn, and remember.

A read-back arrives in a shape we do not recognise. In order:

1. A known `system_profiles` mapping that fits this response is applied directly.
2. Otherwise Atlas Vector Search looks in `cases` (filtered on system and
   failure_type) for a past fix. A matching case's mapping is validated and
   applied: one step.
3. Otherwise the model is asked for a field mapping, as JSON, and nothing else.

Code validates every mapping, from whatever source: each target must be a
canonical field, each source must exist in the response, and the mapped
operation_id must equal the one we asked for. The model never marks anything
verified; the ledger's own comparison of intended versus held still decides.

On success we save a new `system_profiles` version, write a `cases` document
(when the fix is new) and retry only that read. Every step is appended to the
`timeline` collection so the page can show it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .. import config
from .. import db as dbm

CANONICAL = ("operation_id", "reference", "payload")
FAILURE_TYPE = "schema_mismatch"


class MappingError(ValueError):
    """A proposed mapping failed validation. It is never applied."""


# ---------------------------------------------------------------- validation (pure, no network)

def is_canonical(body: Any) -> bool:
    return isinstance(body, dict) and all(k in body for k in CANONICAL)


def validate_mapping(mapping: Any, body: dict[str, Any], op_id: str) -> dict[str, str]:
    """Check a proposed {canonical_field: response_field} mapping. Returns it cleaned, or raises."""
    if not isinstance(mapping, dict) or not mapping:
        raise MappingError("mapping must be a non-empty JSON object")
    targets = set(mapping)
    unknown = targets - set(CANONICAL)
    if unknown:
        raise MappingError(f"targets are not canonical fields: {sorted(unknown)}")
    missing = set(CANONICAL) - targets
    if missing:
        raise MappingError(f"mapping does not cover: {sorted(missing)}")
    for target, source in mapping.items():
        if not isinstance(source, str):
            raise MappingError(f"source for {target} must be a field name")
        if source not in body:
            raise MappingError(f"source field {source!r} is not in the response")
    if len(set(mapping.values())) != len(mapping):
        raise MappingError("two targets map to the same source field")
    applied = apply_mapping(body, mapping)
    if applied["operation_id"] != op_id:
        raise MappingError(f"mapped operation_id {applied['operation_id']!r} is not the one asked for")
    if not isinstance(applied["payload"], dict):
        raise MappingError("mapped payload is not an object")
    return {t: mapping[t] for t in CANONICAL}


def apply_mapping(body: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    return {target: body[source] for target, source in mapping.items()}


def parse_model_json(text: str) -> Any:
    """Pull the first JSON object out of the model's answer (it may wrap it in a code fence)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise MappingError("model did not return a JSON object")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise MappingError(f"model returned invalid JSON: {exc}") from exc


def describe(system: str, keys: list[str]) -> str:
    """The text a case is embedded and searched by. Same template for writing and querying."""
    return (f"{system} read-back response has unrecognised field names {', '.join(sorted(keys))}; "
            f"expected canonical fields {', '.join(CANONICAL)}. Schema mismatch: fields renamed.")


# ---------------------------------------------------------------- embeddings

def local_embed(text: str, dims: int = dbm.LOCAL_EMBED_DIMS) -> list[float]:
    """Fallback only. Deterministic hashed bag of words, L2-normalised.

    Used when the cluster does not support Atlas automated embedding (see
    db.ensure_vector_index). On our M10 sandbox automated embedding (voyage-4)
    works, so this path is normally unused.
    """
    vec = [0.0] * dims
    for tok in re.findall(r"[a-z0-9_]+", text.lower()):
        h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
        vec[h % dims] += 1.0 if (h >> 63) == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# ---------------------------------------------------------------- Atlas: timeline, cases, profiles

def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(task_id: str, system: str, kind: str, detail: dict[str, Any], started: float) -> None:
    dbm.db()[dbm.TIMELINE].insert_one({
        "task_id": task_id, "system": system, "at": _now(), "kind": kind, "detail": detail,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    })


def search_cases(system: str, keys: list[str], limit: int = 3) -> list[dict[str, Any]]:
    """Atlas $vectorSearch over `cases`, filtered on system and failure_type."""
    mode = dbm.ensure_vector_index(wait_seconds=60)
    if mode is None:
        return []  # index not queryable in time: fall through to the model
    text = describe(system, keys)
    stage: dict[str, Any] = {
        "index": dbm.CASES_VECTOR_INDEX, "numCandidates": 30, "limit": limit,
        "filter": {"$and": [{"system": {"$eq": system}}, {"failure_type": {"$eq": FAILURE_TYPE}}]},
    }
    if mode == "auto":
        stage.update(path="description", query={"text": text})
    else:
        stage.update(path="embedding", queryVector=local_embed(text))
    return list(dbm.db()[dbm.CASES].aggregate([
        {"$vectorSearch": stage},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]))


def write_case(system: str, keys: list[str], mapping: dict[str, str], task_id: str) -> None:
    doc: dict[str, Any] = {
        "system": system, "failure_type": FAILURE_TYPE, "description": describe(system, keys),
        "response_fields": sorted(keys), "mapping": mapping, "learned_from_task": task_id,
        "created_at": _now(), "times_used": 0,
    }
    if dbm.vector_mode() == "local":
        doc["embedding"] = local_embed(doc["description"])
    # In "auto" mode Atlas embeds `description` itself (voyage-4) and keeps the vector.
    dbm.db()[dbm.CASES].insert_one(doc)


def latest_profile(system: str) -> dict[str, Any] | None:
    return dbm.db()[dbm.SYSTEM_PROFILES].find_one({"system": system}, sort=[("version", -1)])


def save_profile(system: str, mapping: dict[str, str], source: str, task_id: str) -> int:
    prev = latest_profile(system)
    version = (prev["version"] + 1) if prev else 1
    dbm.db()[dbm.SYSTEM_PROFILES].insert_one({
        "system": system, "version": version, "read_back_fields": mapping, "source": source,
        "learned_from_task": task_id, "created_at": _now(),
    })
    return version


# ---------------------------------------------------------------- the model

def ask_model(system: str, body: dict[str, Any]) -> Any:
    """Ask Claude for a mapping only. It sees field names and value types, never decides anything."""
    from langchain_anthropic import ChatAnthropic

    shape = {k: type(v).__name__ for k, v in body.items()}
    prompt = (
        f"The '{system}' system answered a read-back with this shape (field name: value type):\n"
        f"{json.dumps(shape)}\n\n"
        "We expect exactly these canonical fields: operation_id (string, the operation we asked about), "
        "reference (string, the system's own reference), payload (object, what the system holds).\n\n"
        "Reply with ONLY a JSON object mapping each canonical field to the response field that holds it, "
        'like {"operation_id": "...", "reference": "...", "payload": "..."}. No other text.'
    )
    # No temperature: newer models reject non-default values. Code validates the answer anyway.
    llm = ChatAnthropic(model=config.MODEL, api_key=config.ANTHROPIC_API_KEY, max_tokens=200, timeout=30)
    msg = llm.invoke(prompt)
    content = msg.content if isinstance(msg.content, str) else "".join(
        part.get("text", "") for part in msg.content if isinstance(part, dict))
    return parse_model_json(content)


# ---------------------------------------------------------------- the recovery

def recover(system: str, op_id: str, body: dict[str, Any],
            refetch: Callable[[], dict[str, Any] | None],
            model: Callable[[str, dict[str, Any]], Any] = ask_model) -> dict[str, Any] | None:
    """Turn an unrecognised read-back into the canonical shape, or raise MappingError.

    Returns the canonical body from a retried read (or None if the retry finds nothing).
    """
    task_id = op_id.rsplit(":", 1)[0]
    keys = sorted(body)

    # A profile we already learned for this system, if it fits this response: apply it silently.
    profile = latest_profile(system)
    if profile:
        try:
            validate_mapping(profile["read_back_fields"], body, op_id)
            return _retry(refetch, profile["read_back_fields"], op_id)
        except MappingError:
            pass  # the system changed again; recover below

    started = time.perf_counter()
    record(task_id, system, "schema_change_detected",
           {"response_fields": keys, "expected": list(CANONICAL), "operation_id": op_id}, started)

    mapping: dict[str, str] | None = None
    source = "model"
    try:
        hits = search_cases(system, keys)
    except Exception as exc:  # noqa: BLE001 - search failing must not stop recovery
        hits, search_error = [], str(exc)[:200]
    else:
        search_error = None
    for hit in hits:
        try:
            mapping = validate_mapping(hit.get("mapping"), body, op_id)
        except MappingError:
            continue
        source = "case"
        dbm.db()[dbm.CASES].update_one({"_id": hit["_id"]}, {"$inc": {"times_used": 1}})
        record(task_id, system, "case_found",
               {"case_id": str(hit["_id"]), "score": round(hit.get("score", 0.0), 4),
                "description": hit.get("description"), "mapping": mapping}, started)
        break

    if mapping is None:
        try:
            proposed = model(system, body)
        except Exception as exc:  # noqa: BLE001 - record it, then let the ledger say "unable to determine"
            record(task_id, system, "model_mapping", {"error": str(exc)[:300], "model": config.MODEL}, started)
            raise MappingError(f"model gave no mapping: {exc}") from exc
        record(task_id, system, "model_mapping",
               {"proposed": proposed, "cases_searched": len(hits), "search_error": search_error,
                "model": config.MODEL}, started)
        mapping = validate_mapping(proposed, body, op_id)  # raises MappingError if unsafe

    version = save_profile(system, mapping, source, task_id)
    if source == "model":
        write_case(system, keys, mapping, task_id)
    record(task_id, system, "mapping_validated",
           {"mapping": mapping, "source": source, "profile_version": version,
            "case_written": source == "model",
            # steps = mapping sources tried: a past case (1), or a search miss then the model (2)
            "steps": 1 if source == "case" else 2}, started)
    return _retry(refetch, mapping, op_id)


def _retry(refetch: Callable[[], dict[str, Any] | None], mapping: dict[str, str], op_id: str):
    """Retry only this read. Canonical: use it. Still renamed: apply the validated mapping."""
    again = refetch()
    if again is None or is_canonical(again):
        return again
    validate_mapping(mapping, again, op_id)
    return apply_mapping(again, mapping)
