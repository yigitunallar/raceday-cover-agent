"""Calls to the outside systems. Only the ledger uses these for writes."""
from typing import Any

import httpx

from .. import config
from . import schema_recovery


def send(system: str):
    def _call(op_id: str, payload: dict[str, Any]) -> Any:
        r = httpx.post(f"{config.MOCKS_URL}/{system}/operations",
                       json={"operation_id": op_id, "payload": payload}, timeout=10)
        r.raise_for_status()
        return r.json()
    return _call


def look_up(system: str):
    def _fetch(op_id: str) -> dict[str, Any] | None:
        r = httpx.get(f"{config.MOCKS_URL}/{system}/operations/{op_id}", timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def _read(op_id: str) -> dict[str, Any] | None:
        body = _fetch(op_id)
        if body is None or schema_recovery.is_canonical(body):
            return body
        # The system changed its field names. Find a past fix (Vector Search) or ask
        # the model for a mapping; code validates it, saves a new system_profiles
        # version, writes a case, and retries only this read. Raises MappingError
        # (the ledger then records "unable to determine") if no safe mapping exists.
        return schema_recovery.recover(system, op_id, body, refetch=lambda: _fetch(op_id))
    return _read
