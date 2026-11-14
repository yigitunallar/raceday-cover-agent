"""Mapping validation for the "system changed its fields" recovery. No network, no Atlas."""
import pytest

from raceday.agent.schema_recovery import (
    MappingError, apply_mapping, describe, is_canonical, local_embed, parse_model_json, validate_mapping,
)

OP = "task-7:report"
RENAMED = {"op_id": OP, "ref": "ins_00001234", "data": {"task_id": "task-7", "sum_insured": 9000}}
GOOD = {"operation_id": "op_id", "reference": "ref", "payload": "data"}


def test_good_mapping_is_accepted_and_applied():
    assert validate_mapping(GOOD, RENAMED, OP) == GOOD
    out = apply_mapping(RENAMED, GOOD)
    assert is_canonical(out)
    assert out["payload"]["sum_insured"] == 9000


def test_target_must_be_canonical():
    with pytest.raises(MappingError, match="not canonical"):
        validate_mapping({**GOOD, "verified": "ref"}, RENAMED, OP)


def test_every_canonical_field_must_be_covered():
    with pytest.raises(MappingError, match="does not cover"):
        validate_mapping({"operation_id": "op_id", "payload": "data"}, RENAMED, OP)


def test_source_must_exist_in_the_response():
    with pytest.raises(MappingError, match="not in the response"):
        validate_mapping({**GOOD, "reference": "reference_no"}, RENAMED, OP)


def test_operation_id_must_be_the_one_asked_for():
    swapped = {"operation_id": "ref", "reference": "op_id", "payload": "data"}
    with pytest.raises(MappingError, match="not the one asked for"):
        validate_mapping(swapped, RENAMED, OP)
    with pytest.raises(MappingError, match="not the one asked for"):
        validate_mapping(GOOD, {**RENAMED, "op_id": "task-8:report"}, OP)


def test_payload_must_be_an_object():
    with pytest.raises(MappingError, match="payload"):
        validate_mapping(GOOD, {**RENAMED, "data": "covered"}, OP)


def test_two_targets_cannot_share_a_source():
    body = {**RENAMED, "extra": OP}
    with pytest.raises(MappingError):
        validate_mapping({"operation_id": "op_id", "reference": "op_id", "payload": "data"}, body, OP)


@pytest.mark.parametrize("bad", [None, [], "op_id", {}, {"operation_id": 1, "reference": "ref", "payload": "data"}])
def test_garbage_is_rejected(bad):
    with pytest.raises(MappingError):
        validate_mapping(bad, RENAMED, OP)


def test_model_json_is_parsed_from_a_code_fence():
    text = '```json\n{"operation_id": "op_id", "reference": "ref", "payload": "data"}\n```'
    assert parse_model_json(text) == GOOD
    with pytest.raises(MappingError):
        parse_model_json("I think op_id is the operation id.")


def test_local_embedding_is_deterministic_and_normalised():
    a = local_embed(describe("insurer", ["data", "op_id", "ref"]))
    assert a == local_embed(describe("insurer", ["ref", "op_id", "data"]))
    assert len(a) == 256
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9
