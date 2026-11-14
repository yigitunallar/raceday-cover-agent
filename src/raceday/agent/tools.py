"""Calls to the outside systems. Only the ledger uses these for writes."""
from typing import Any

import httpx

from .. import config


def send(system: str):
    def _call(op_id: str, payload: dict[str, Any]) -> Any:
        r = httpx.post(f"{config.MOCKS_URL}/{system}/operations",
                       json={"operation_id": op_id, "payload": payload}, timeout=10)
        r.raise_for_status()
        return r.json()
    return _call


def look_up(system: str):
    def _read(op_id: str) -> dict[str, Any] | None:
        r = httpx.get(f"{config.MOCKS_URL}/{system}/operations/{op_id}", timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        if "payload" not in body:
            # TODO(agent): the system changed its field names. This is where the model
            # reads the new response, proposes a mapping, saves a new system_profiles
            # version, writes a case for Vector Search, and retries only this step.
            raise ValueError(f"unrecognised response shape from {system}: {sorted(body)}")
        return body
    return _read
