"""Tests for structured processing-error records, their validator, and failure responses.

All identifiers and values are synthetic.
"""
import copy
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.processing_errors import (  # noqa: E402
    DIAGNOSTIC_REASONS,
    ERROR_TYPES,
    FAILURE_CODES,
    RECORD_FIELDS,
    build_failure_response,
    initialize_processing_run,
    validate_processing_errors,
)
from grantcheck.orchestrator import process_application  # noqa: E402

RUN = "12345678-1234-4234-8234-123456789abc"
OTHER_RUN = "87654321-4321-4321-8321-cba987654321"
RESPONSE_KEYS = {"processing_run_id", "failure_code", "rule_results", "diagnostics"}


def record(**overrides):
    base = {
        "error_type": "extraction_failure",
        "document": "budget_justification",
        "fact_category": "project_duration_months",
        "processing_run_id": RUN,
    }
    base.update(overrides)
    return base


def diagnostics_of(records, run=RUN):
    return [tuple(d.values()) for d in validate_processing_errors(records, run)["diagnostics"]]


# --- Schema constants -----------------------------------------------------------


def test_controlled_vocabularies():
    assert ERROR_TYPES == ("document_processing_failure", "extraction_failure", "internal_processing_failure")
    assert RECORD_FIELDS == ("error_type", "document", "fact_category", "processing_run_id")
    assert set(DIAGNOSTIC_REASONS) == {
        "unsupported_identifier", "invalid_scope_combination", "unsupported_error_type",
        "invalid_record_structure", "run_identity_mismatch",
    }
    assert FAILURE_CODES == ("invalid_processing_error_record", "application_integrity_failure", "fatal_internal_failure")


# --- Valid records --------------------------------------------------------------


@pytest.mark.parametrize("valid", [
    record(error_type="document_processing_failure", document="budget", fact_category=None),     # document-level
    record(),                                                                                     # fact-level
    record(error_type="internal_processing_failure", document=None, fact_category=None),         # application-level
    record(document="project_narrative", fact_category="project_duration_months"),
    record(error_type="extraction_failure", document="budget", fact_category="personnel_role"),
], ids=["document_level", "fact_level", "application_level", "narrative_duration", "budget_role"])
def test_valid_records_are_accepted(valid):
    assert validate_processing_errors([valid], RUN) == {"valid": True, "evaluation_permitted": True, "diagnostics": []}


def test_empty_collection_and_tuples_are_valid():
    assert validate_processing_errors([], RUN)["valid"] is True
    assert validate_processing_errors((record(),), RUN)["valid"] is True


@pytest.mark.parametrize("submitted, authoritative", [
    (RUN.upper(), RUN), (RUN, RUN.upper()), ("12345678-1234-4234-8234-123456789ABC", RUN),
])
def test_run_id_capitalization_is_equivalent(submitted, authoritative):
    assert validate_processing_errors([record(processing_run_id=submitted)], authoritative)["valid"] is True


def test_validation_does_not_modify_or_rewrite_records():
    records = [record(processing_run_id=RUN.upper()), record(error_type="bogus")]
    before = copy.deepcopy(records)
    validate_processing_errors(records, RUN)
    assert records == before
    assert records[0]["processing_run_id"] == RUN.upper()


# --- Single-record problems -----------------------------------------------------


@pytest.mark.parametrize("bad, expected", [
    (record(error_type="parse_failure"), [(0, "error_type", "unsupported_error_type")]),
    (record(error_type=5), [(0, "error_type", "invalid_record_structure")]),
    (record(error_type=None), [(0, "error_type", "invalid_record_structure")]),
    (record(document="appendix"), [(0, "document", "unsupported_identifier")]),
    (record(document="Budget"), [(0, "document", "unsupported_identifier")]),
    (record(document=1), [(0, "document", "unsupported_identifier")]),
    (record(fact_category="travel_purpose"), [(0, "fact_category", "unsupported_identifier")]),
    (record(fact_category=["project_duration_months"]), [(0, "fact_category", "unsupported_identifier")]),
    (record(document=None), [(0, "fact_category", "invalid_scope_combination")]),
    (record(document="budget", fact_category="travel_justification"), [(0, "fact_category", "invalid_scope_combination")]),
    (record(processing_run_id="not-a-uuid"), [(0, "processing_run_id", "invalid_record_structure")]),
    (record(processing_run_id=None), [(0, "processing_run_id", "invalid_record_structure")]),
    (record(processing_run_id="{" + RUN + "}"), [(0, "processing_run_id", "invalid_record_structure")]),
    (record(processing_run_id=RUN.replace("-", "")), [(0, "processing_run_id", "invalid_record_structure")]),
    (record(processing_run_id=uuid.UUID(RUN)), [(0, "processing_run_id", "invalid_record_structure")]),
    (record(processing_run_id=OTHER_RUN), [(0, "processing_run_id", "run_identity_mismatch")]),
    (record(processing_run_id=OTHER_RUN.upper()), [(0, "processing_run_id", "run_identity_mismatch")]),
    ({**record(), "message": "x"}, [(0, None, "invalid_record_structure")]),
], ids=[
    "unsupported_error_type", "error_type_number", "error_type_null", "unsupported_document",
    "document_wrong_case", "document_number", "unsupported_fact_category", "fact_category_list",
    "fact_without_document", "fact_not_sourced_from_document", "malformed_uuid", "null_uuid", "braced_uuid",
    "uuid_without_hyphens", "uuid_object", "mismatched_uuid", "mismatched_uuid_uppercase", "extra_field",
])
def test_invalid_record_is_rejected(bad, expected):
    result = validate_processing_errors([bad], RUN)
    assert result["valid"] is False
    assert result["evaluation_permitted"] is False
    assert [tuple(d.values()) for d in result["diagnostics"]] == expected


@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_missing_field_is_rejected(field):
    bad = record()
    del bad[field]
    assert (0, field, "invalid_record_structure") in diagnostics_of([bad])


@pytest.mark.parametrize("bad", ["record", None, [], 42, ("error_type",)], ids=repr)
def test_non_dict_record_is_rejected(bad):
    assert diagnostics_of([record(), bad]) == [(1, None, "invalid_record_structure")]


@pytest.mark.parametrize("collection", [None, "records", {"0": record()}, 3, record()], ids=repr)
def test_non_list_collection_is_rejected(collection):
    assert diagnostics_of(collection) == [(None, None, "invalid_record_structure")]


@pytest.mark.parametrize("authoritative", [None, "", "not-a-uuid", 7, uuid.UUID(RUN)], ids=repr)
def test_invalid_authoritative_run_id_is_a_caller_error(authoritative):
    with pytest.raises(ValueError, match="authoritative"):
        validate_processing_errors([record()], authoritative)


# --- Multiple problems, ordering, deduplication -----------------------------------


def test_all_problems_are_collected_in_deterministic_order():
    worst = {"extra_b": 1, "processing_run_id": OTHER_RUN, "fact_category": "travel_purpose",
             "document": "appendix", "error_type": "bogus", "extra_a": 2}
    records = [record(), worst, "not a record", record(processing_run_id="bad")]

    assert diagnostics_of(records) == [
        (1, None, "invalid_record_structure"),          # extra fields, reported once
        (1, "error_type", "unsupported_error_type"),
        (1, "document", "unsupported_identifier"),
        (1, "fact_category", "unsupported_identifier"),
        (1, "processing_run_id", "run_identity_mismatch"),
        (2, None, "invalid_record_structure"),
        (3, "processing_run_id", "invalid_record_structure"),
    ]


def test_ordering_ignores_key_order_and_preserves_indices():
    forward = record(error_type="bogus", document="appendix")
    backward = dict(reversed(list(forward.items())))
    assert diagnostics_of([record(), forward]) == diagnostics_of([record(), backward])
    assert {index for index, _, _ in diagnostics_of([record(), record(), forward])} == {2}


def test_identical_diagnostics_are_deduplicated():
    bad = {**record(), "extra_1": 1, "extra_2": 2, "extra_3": 3}
    assert diagnostics_of([bad]) == [(0, None, "invalid_record_structure")]


def test_diagnostics_from_different_records_are_kept_separately():
    assert diagnostics_of([record(error_type="bogus"), record(error_type="bogus")]) == [
        (0, "error_type", "unsupported_error_type"),
        (1, "error_type", "unsupported_error_type"),
    ]


def test_missing_and_extra_fields_together():
    bad = {"error_type": "extraction_failure", "unexpected": True}
    assert diagnostics_of([bad]) == [
        (0, None, "invalid_record_structure"),
        (0, "document", "invalid_record_structure"),
        (0, "fact_category", "invalid_record_structure"),
        (0, "processing_run_id", "invalid_record_structure"),
    ]


def test_diagnostics_never_contain_rejected_values():
    canary = "CANARY-7f3a document text: Project Duration: 36 months"
    records = [
        {"error_type": canary, "document": canary, "fact_category": canary, "processing_run_id": canary,
         canary: canary},
        record(processing_run_id=OTHER_RUN),
    ]
    result = validate_processing_errors(records, RUN)
    serialized = json.dumps(result)

    assert "CANARY-7f3a" not in serialized
    assert "36 months" not in serialized
    assert OTHER_RUN not in serialized
    for diagnostic in result["diagnostics"]:
        assert set(diagnostic) == {"record_index", "field", "reason"}
        assert diagnostic["field"] in (None,) + RECORD_FIELDS
        assert diagnostic["reason"] in DIAGNOSTIC_REASONS


# --- Failure responses ------------------------------------------------------------


@pytest.mark.parametrize("code", FAILURE_CODES)
def test_failure_response_schema(code):
    response = build_failure_response(code, RUN)
    assert set(response) == RESPONSE_KEYS
    assert response == {"processing_run_id": RUN, "failure_code": code, "rule_results": None, "diagnostics": []}


def test_failure_response_carries_validator_diagnostics_and_null_rule_results():
    validation = validate_processing_errors([record(processing_run_id=OTHER_RUN)], RUN)
    response = build_failure_response("invalid_processing_error_record", RUN, validation["diagnostics"])

    assert response["rule_results"] is None
    assert response["diagnostics"] == validation["diagnostics"]
    assert response["diagnostics"] is not validation["diagnostics"]
    assert OTHER_RUN not in json.dumps(response)


def test_failure_response_keeps_the_authoritative_id_unchanged():
    assert build_failure_response("application_integrity_failure", RUN.upper())["processing_run_id"] == RUN.upper()


@pytest.mark.parametrize("code", ["", "invalid", "FATAL_INTERNAL_FAILURE", None])
def test_unknown_failure_code_is_rejected(code):
    with pytest.raises(ValueError):
        build_failure_response(code, RUN)


@pytest.mark.parametrize("run_id", ["not-a-uuid", 5, uuid.UUID(RUN)], ids=repr)
def test_invalid_response_run_id_is_rejected(run_id):
    with pytest.raises(ValueError):
        build_failure_response("fatal_internal_failure", run_id)


@pytest.mark.parametrize("bad_diagnostic", [
    {"record_index": 0, "field": "error_type", "reason": "unsupported_error_type", "value": "SECRET"},
    {"record_index": 0, "field": "error_type", "reason": "SECRET reason"},
    {"record_index": 0, "field": "SECRET field", "reason": "invalid_record_structure"},
    {"record_index": -1, "field": None, "reason": "invalid_record_structure"},
    {"record_index": True, "field": None, "reason": "invalid_record_structure"},
    "SECRET",
], ids=["extra_key", "bad_reason", "bad_field", "negative_index", "bool_index", "not_a_dict"])
def test_unsanitized_diagnostics_are_refused(bad_diagnostic):
    with pytest.raises(ValueError) as error:
        build_failure_response("invalid_processing_error_record", RUN, [bad_diagnostic])
    assert "SECRET" not in str(error.value)


# --- Run initialization and evaluation safety -------------------------------------


def test_initialization_with_injected_provider():
    assert initialize_processing_run(lambda: uuid.UUID(RUN)) == (RUN, None)


def test_initialization_with_default_provider():
    run_id, failure = initialize_processing_run()
    assert failure is None
    assert str(uuid.UUID(run_id)) == run_id


def test_initialization_failure_returns_fatal_response_without_run_id():
    def broken():
        raise RuntimeError("TRACE-DETAIL-991")

    run_id, failure = initialize_processing_run(broken)

    assert run_id is None
    assert failure == {
        "processing_run_id": None, "failure_code": "fatal_internal_failure",
        "rule_results": None, "diagnostics": [],
    }
    assert "TRACE-DETAIL-991" not in json.dumps(failure)


def test_invalid_records_forbid_evaluation():
    validation = validate_processing_errors([record(), record(document="appendix")], RUN)
    assert validation["valid"] is False
    assert validation["evaluation_permitted"] is False


# --- Replay workflow is unchanged -------------------------------------------------


def test_replay_result_has_no_run_identity_or_failure_fields():
    scenario = ROOT / "sample_data" / "S01_consistent"
    result = process_application(
        "GC-S01", scenario / "budget.txt", scenario / "budget_justification.txt",
        scenario / "project_narrative.txt", scenario / "replay_facts.json",
    )
    assert set(result) == {
        "application_id", "processing_status", "review_required", "candidate_facts",
        "rule_results", "finding_count", "errors", "provenance",
    }
    assert "processing_run_id" not in json.dumps(result)
