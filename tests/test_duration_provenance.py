"""Tests for extracted-duration provenance verification and safe fact isolation.

Synthetic documents go through real document intake; synthetic extraction
results are produced by the real extractor and then altered one fault at a time.
"""
import copy
import dataclasses
import itertools
import json
import sys
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.document_intake import DOCUMENT_ROLES, intake_application  # noqa: E402
from grantcheck.duration_extraction import DURATION_CATEGORY, extract_candidate_facts  # noqa: E402
from grantcheck.duration_provenance import (  # noqa: E402
    ATTEMPT_REASONS,
    DiagnosticAttempt,
    verify_duration_extraction,
)
from grantcheck.fact_schema import FACT_SOURCES  # noqa: E402
from grantcheck.fact_validation import validate_candidate_facts  # noqa: E402
from grantcheck.processing_errors import validate_processing_errors  # noqa: E402
from grantcheck.run_identity import RunIdentityError  # noqa: E402

RUN = "12345678-1234-4234-8234-123456789abc"
FILENAMES = {"budget": "budget.txt", "budget_justification": "budget_justification.txt",
             "project_narrative": "project_narrative.txt"}
RESULT_KEYS = {"application_integrity_failure", "candidate_facts", "not_found_reasons",
               "verified_evidence", "processing_errors", "diagnostic_attempts"}
UNPROCESSABLE = {"value": None, "status": "unprocessable", "evidence_text": None}
CANARY = "CANARY-41f9"

DOCUMENTS = {
    "budget": ["BUDGET", "Project Duration: 24 months", "Travel Cost: $8,000"],
    "budget_justification": ["JUSTIFICATION", "Duration: 2 years", "Duration: 24 months"],
    "project_narrative": ["NARRATIVE", "The research will be conducted over two years."],
}


def sequential_ids():
    counter = itertools.count(1)
    return lambda: uuid.UUID(int=next(counter))


@pytest.fixture
def make_application(tmp_path):
    counter = itertools.count()

    def make(documents=None, newline="\n"):
        documents = documents or DOCUMENTS
        folder = tmp_path / f"app{next(counter)}"
        folder.mkdir()
        paths = []
        for role in DOCUMENT_ROLES:
            path = folder / FILENAMES[role]
            path.write_bytes((newline.join(documents[role]) + newline).encode("utf-8"))
            paths.append(path)
        application = intake_application("GC-TEST", *paths)
        assert application["intake_status"] == "completed", "test setup: intake failed"
        return application
    return make


@pytest.fixture
def scenario(make_application):
    application = make_application()
    return application, extract_candidate_facts(application)


def verify(application, extraction, run=RUN):
    return verify_duration_extraction(application, extraction, run, attempt_id_factory=sequential_ids())


def reasons_of(result):
    return [(a.document, a.fact_category, a.reason) for a in result["diagnostic_attempts"]]


def assert_isolated(result, role, reason):
    """Only the duration fact for role was isolated, for the given reason."""
    assert result["application_integrity_failure"] is False
    assert result["candidate_facts"][DURATION_CATEGORY][role] == UNPROCESSABLE
    assert result["verified_evidence"][role] == []
    assert result["processing_errors"] == [{
        "error_type": "extraction_failure", "document": role,
        "fact_category": DURATION_CATEGORY, "processing_run_id": RUN,
    }]
    assert reasons_of(result) == [(role, DURATION_CATEGORY, reason)]


# --- Correct evidence ------------------------------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_correct_extraction_verifies_cleanly(make_application, newline):
    application = make_application(newline=newline)
    extraction = extract_candidate_facts(application)

    result = verify(application, extraction)

    assert set(result) == RESULT_KEYS
    assert result["application_integrity_failure"] is False
    assert result["candidate_facts"] == extraction["candidate_facts"]
    assert result["verified_evidence"] == extraction["duration_evidence"]
    assert result["not_found_reasons"] == extraction["not_found_reasons"]
    assert result["processing_errors"] == []
    assert result["diagnostic_attempts"] == ()
    validate_candidate_facts(result["candidate_facts"])


def test_repeated_statements_are_all_verified(make_application):
    application = make_application({**DOCUMENTS, "budget": ["Duration: 24 months", "x", "Duration: 24 months"]})
    result = verify(application, extract_candidate_facts(application))
    assert len(result["verified_evidence"]["budget"]) == 2
    assert result["processing_errors"] == []


def test_identical_text_elsewhere_does_not_add_locations(make_application):
    # The excerpt also occurs inside a sentence; only the recorded statement is a location.
    budget = ["Project Duration: 24 months", "Notes: see Project Duration: 24 months above."]
    application = make_application({**DOCUMENTS, "budget": budget})
    result = verify(application, extract_candidate_facts(application))
    assert [o["start_offset"] for o in result["verified_evidence"]["budget"]] == [0]


def test_substring_overlap_example(make_application):
    # "Duration: 24 months" is also a substring of the Project line; offsets keep them distinct.
    application = make_application({**DOCUMENTS, "budget": ["Duration: 24 months", "Project Duration: 24 months"]})
    extraction = extract_candidate_facts(application)
    result = verify(application, extraction)

    assert [o["start_offset"] for o in result["verified_evidence"]["budget"]] == [0, 20]
    assert result["processing_errors"] == []

    # A forged occurrence at the substring inside "Project Duration:" is rejected.
    forged = copy.deepcopy(extraction)
    forged["duration_evidence"]["budget"][1].update(
        start_offset=28, end_offset=47, excerpt="Duration: 24 months")
    assert application["documents"]["budget"]["text"][28:47] == "Duration: 24 months"
    assert_isolated(verify(application, forged), "budget", "not_a_statement")


# --- Occurrence faults -------------------------------------------------------------


def occurrence_fault(**changes):
    def apply(extraction):
        extraction["duration_evidence"]["budget"][0].update(changes)
    return apply


def budget_occurrence(scenario_application):
    return extract_candidate_facts(scenario_application)["duration_evidence"]["budget"][0]


OCCURRENCE_FAULTS = [
    ("shifted_offsets", lambda o: {"start_offset": o["start_offset"] + 1, "end_offset": o["end_offset"] + 1},
     "excerpt_mismatch"),
    ("end_beyond_text", lambda o: {"end_offset": 10_000}, "invalid_offsets"),
    ("negative_start", lambda o: {"start_offset": -1}, "invalid_offsets"),
    ("empty_span", lambda o: {"end_offset": o["start_offset"]}, "invalid_offsets"),
    ("reversed_span", lambda o: {"start_offset": o["end_offset"], "end_offset": o["start_offset"]}, "invalid_offsets"),
    ("boolean_offset", lambda o: {"start_offset": False}, "invalid_offsets"),
    ("string_offset", lambda o: {"end_offset": str(o["end_offset"])}, "invalid_offsets"),
    ("altered_excerpt", lambda o: {"excerpt": "Project Duration: 36 months"}, "excerpt_mismatch"),
    ("non_string_excerpt", lambda o: {"excerpt": None}, "excerpt_mismatch"),
    ("truncated_excerpt", lambda o: {"excerpt": o["excerpt"][:-1], "end_offset": o["end_offset"] - 1},
     "not_a_statement"),
    ("wrong_document", lambda o: {"document": "project_narrative"}, "document_mismatch"),
    ("unsupported_document", lambda o: {"document": "appendix"}, "document_mismatch"),
    ("unsupported_category", lambda o: {"fact_category": "travel_cost_usd"}, "unsupported_fact_category"),
    ("wrong_line", lambda o: {"start_line": o["start_line"] + 1}, "line_mismatch"),
    ("boolean_line", lambda o: {"start_line": True}, "line_mismatch"),
    ("unsupported_match", lambda o: {"match": "probable"}, "unsupported_match"),
    ("valid_zero_value", lambda o: {"value": 0}, "invalid_normalized_value"),
    ("valid_null_value", lambda o: {"value": None}, "invalid_normalized_value"),
    ("valid_boolean_value", lambda o: {"value": True}, "invalid_normalized_value"),
    ("valid_wrong_value", lambda o: {"value": 36}, "invalid_normalized_value"),
    ("ambiguous_with_value", lambda o: {"match": "ambiguous", "value": 24}, "invalid_normalized_value"),
    ("ambiguous_for_valid_text", lambda o: {"match": "ambiguous", "value": None}, "invalid_normalized_value"),
]


@pytest.mark.parametrize("make_changes, reason",
                         [(changes, reason) for _, changes, reason in OCCURRENCE_FAULTS],
                         ids=[case_id for case_id, _, _ in OCCURRENCE_FAULTS])
def test_faulty_occurrence_isolates_its_fact(scenario, make_changes, reason):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0].update(make_changes(extraction["duration_evidence"]["budget"][0]))
    assert_isolated(verify(application, extraction), "budget", reason)


@pytest.mark.parametrize("bad", ["occurrence", None, 3, []], ids=repr)
def test_non_dict_occurrence_is_malformed(scenario, bad):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0] = bad
    assert_isolated(verify(application, extraction), "budget", "malformed_occurrence")


@pytest.mark.parametrize("edit", [
    lambda o: o.pop("start_line"),
    lambda o: o.update(confidence=0.9),
], ids=["missing_key", "extra_key"])
def test_occurrence_with_wrong_keys_is_malformed(scenario, edit):
    application, extraction = scenario
    edit(extraction["duration_evidence"]["budget"][0])
    assert_isolated(verify(application, extraction), "budget", "malformed_occurrence")


@pytest.mark.parametrize("evidence", [None, "list", {"0": {}}], ids=repr)
def test_occurrence_collection_that_is_not_a_list_is_malformed(scenario, evidence):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"] = evidence
    assert_isolated(verify(application, extraction), "budget", "malformed_occurrence")


def test_missing_role_evidence_is_isolated(scenario):
    application, extraction = scenario
    del extraction["duration_evidence"]["budget"]
    assert_isolated(verify(application, extraction), "budget", "malformed_occurrence")


def test_duplicate_occurrence_is_rejected(scenario):
    application, extraction = scenario
    occurrences = extraction["duration_evidence"]["budget"]
    occurrences.append(copy.deepcopy(occurrences[0]))
    assert_isolated(verify(application, extraction), "budget", "duplicate_occurrence")


def test_every_faulty_occurrence_gets_its_own_attempt(scenario):
    application, extraction = scenario
    first, second = extraction["duration_evidence"]["budget_justification"]
    first["start_line"] = 99
    second["excerpt"] = "Duration: 25 months"

    result = verify(application, extraction)

    assert [a.reason for a in result["diagnostic_attempts"]] == ["line_mismatch", "excerpt_mismatch"]
    assert result["processing_errors"] == [{
        "error_type": "extraction_failure", "document": "budget_justification",
        "fact_category": DURATION_CATEGORY, "processing_run_id": RUN,
    }], "one public error per isolated fact"


# --- Record-to-evidence relationship -------------------------------------------------


@pytest.mark.parametrize("record", [
    {"value": 36, "status": "present", "evidence_text": "Project Duration: 24 months"},   # value mismatch
    {"value": 24, "status": "present", "evidence_text": "Travel Cost: $8,000"},           # not an occurrence
    {"value": None, "status": "not_found", "evidence_text": None},                        # hides evidence
    {"value": None, "status": "uncertain", "evidence_text": "Project Duration: 24 months"},  # nothing ambiguous
    {"value": None, "status": "missing", "evidence_text": None},
], ids=["value_mismatch", "evidence_not_an_occurrence", "not_found_with_evidence",
        "uncertain_without_ambiguity", "missing"])
def test_record_not_supported_by_evidence_is_isolated(scenario, record):
    application, extraction = scenario
    extraction["candidate_facts"][DURATION_CATEGORY]["budget"] = record
    if record["status"] == "not_found":
        extraction["not_found_reasons"].setdefault(DURATION_CATEGORY, {})["budget"] = "no_supported_pattern_matched"
    assert_isolated(verify(application, extraction), "budget", "record_evidence_mismatch")


def test_present_record_without_any_occurrence_is_isolated(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"] = []
    assert_isolated(verify(application, extraction), "budget", "record_evidence_mismatch")


def test_non_duration_confident_claim_is_isolated(scenario):
    application, extraction = scenario
    extraction["candidate_facts"]["personnel_cost_usd"]["budget"] = {
        "value": 24000, "status": "present", "evidence_text": "Personnel Cost: $24,000"}
    del extraction["not_found_reasons"]["personnel_cost_usd"]["budget"]

    result = verify(application, extraction)

    assert result["candidate_facts"]["personnel_cost_usd"]["budget"] == UNPROCESSABLE
    assert result["processing_errors"] == [{
        "error_type": "extraction_failure", "document": "budget",
        "fact_category": "personnel_cost_usd", "processing_run_id": RUN,
    }]
    assert reasons_of(result) == [("budget", "personnel_cost_usd", "unsupported_fact_extraction")]


# --- Isolation keeps unaffected facts ------------------------------------------------


def test_unaffected_facts_and_evidence_are_preserved(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["start_line"] = 1   # break only the budget

    result = verify(application, extraction)
    facts = result["candidate_facts"]

    assert facts[DURATION_CATEGORY]["budget"] == UNPROCESSABLE
    for role in ("budget_justification", "project_narrative"):
        assert facts[DURATION_CATEGORY][role] == extraction["candidate_facts"][DURATION_CATEGORY][role]
        assert result["verified_evidence"][role] == extraction["duration_evidence"][role]
    for category, roles in FACT_SOURCES.items():
        if category != DURATION_CATEGORY:
            assert facts[category] == extraction["candidate_facts"][category]
    assert result["not_found_reasons"] == extraction["not_found_reasons"]
    validate_candidate_facts(facts)


def test_rejected_evidence_is_excluded_from_verified_output(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["excerpt"] = f"{CANARY} forged"

    result = verify(application, extraction)

    assert result["verified_evidence"]["budget"] == []
    assert CANARY not in json.dumps(result["verified_evidence"])
    assert result["candidate_facts"][DURATION_CATEGORY]["budget"]["evidence_text"] is None


def test_public_errors_contain_no_rejected_values_or_document_text(scenario):
    application, extraction = scenario
    occurrence = extraction["duration_evidence"]["budget"][0]
    occurrence["excerpt"] = f"{CANARY} Project Duration: 99 months"

    result = verify(application, extraction)
    public = json.dumps(result["processing_errors"])

    assert CANARY not in public
    assert "Project Duration" not in public
    assert application["documents"]["budget"]["text"] not in public
    assert validate_processing_errors(result["processing_errors"], RUN)["valid"] is True


# --- Run identity and diagnostic attempts --------------------------------------------


def test_errors_and_attempts_reference_the_authoritative_run(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["start_line"] = 99
    upper = RUN.upper()

    result = verify_duration_extraction(application, extraction, upper, attempt_id_factory=sequential_ids())

    assert result["processing_errors"][0]["processing_run_id"] == upper   # passed through, not rewritten
    assert all(attempt.processing_run_id == upper for attempt in result["diagnostic_attempts"])


def test_attempt_ids_are_unique_uuids(scenario):
    application, extraction = scenario
    for occurrence in extraction["duration_evidence"]["budget_justification"]:
        occurrence["start_line"] = 99
    extraction["duration_evidence"]["budget"][0]["start_line"] = 99

    result = verify_duration_extraction(application, extraction, RUN)   # default uuid4

    ids = [attempt.attempt_id for attempt in result["diagnostic_attempts"]]
    assert len(ids) == 3 == len(set(ids))
    assert all(str(uuid.UUID(attempt_id)) == attempt_id for attempt_id in ids)
    assert RUN not in ids


@pytest.mark.parametrize("factory", [
    lambda: uuid.UUID(RUN),                       # reuses the run ID
    lambda: uuid.UUID(int=1),                     # repeats for the second attempt
    lambda: "12345678-0000-4000-8000-000000000000",
    lambda: (_ for _ in ()).throw(OSError("TRACE")),
], ids=["run_id", "repeated", "string", "raises"])
def test_attempt_id_failure_is_an_identity_error(scenario, factory):
    application, extraction = scenario
    for occurrence in extraction["duration_evidence"]["budget_justification"]:
        occurrence["start_line"] = 99
    with pytest.raises(RunIdentityError) as error:
        verify_duration_extraction(application, extraction, RUN, attempt_id_factory=factory)
    assert "TRACE" not in str(error.value)


def test_attempts_are_restricted_and_immutable(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["excerpt"] = f"{CANARY} forged"

    attempt = verify(application, extraction)["diagnostic_attempts"][0]

    assert isinstance(attempt, DiagnosticAttempt)
    assert attempt.verification_status == "unverified"
    assert attempt.reason in ATTEMPT_REASONS
    assert CANARY not in repr(attempt)
    with pytest.raises(dataclasses.FrozenInstanceError):
        attempt.reason = "excerpt_mismatch"
    assert isinstance(attempt.contents, MappingProxyType)
    with pytest.raises(TypeError):
        attempt.contents["excerpt"] = "repaired"
    with pytest.raises(TypeError):
        json.dumps(attempt)   # cannot leak through ordinary serialization


def test_attempt_contents_are_detached_from_later_changes(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["excerpt"] = f"{CANARY} forged"
    attempt = verify(application, extraction)["diagnostic_attempts"][0]

    extraction["duration_evidence"]["budget"][0]["excerpt"] = "corrected later"

    assert attempt.contents["excerpt"] == f"{CANARY} forged"


def test_attempt_export_is_plain_json(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["excerpt"] = f"{CANARY} forged"
    exported = verify(application, extraction)["diagnostic_attempts"][0].to_export_dict()

    assert set(exported) == {"attempt_id", "processing_run_id", "document", "fact_category",
                             "reason", "verification_status", "contents"}
    assert json.loads(json.dumps(exported)) == exported
    assert exported["contents"]["excerpt"] == f"{CANARY} forged"
    assert exported["verification_status"] == "unverified"


# --- Application-level integrity failures --------------------------------------------


@pytest.mark.parametrize("break_input", [
    lambda app, ext: (app, {k: v for k, v in ext.items() if k != "duration_evidence"}),
    lambda app, ext: (app, {**ext, "extra": 1}),
    lambda app, ext: (app, None),
    lambda app, ext: (app, {**ext, "candidate_facts": {}}),
    lambda app, ext: (app, {**ext, "duration_evidence": {**ext["duration_evidence"], "appendix": []}}),
    lambda app, ext: (app, {**ext, "duration_evidence": []}),
    lambda app, ext: (app, {**ext, "not_found_reasons": []}),
    lambda app, ext: (app, {**ext, "not_found_reasons": {}}),          # reasons lost for not_found records
    lambda app, ext: ({"documents": {}}, ext),
    lambda app, ext: (None, ext),
], ids=["missing_key", "extra_key", "not_a_dict", "invalid_facts", "unknown_role", "evidence_list",
        "reasons_list", "reasons_missing", "documents_missing", "application_none"])
def test_unisolatable_problems_are_application_integrity_failures(scenario, break_input):
    application, extraction = break_input(*scenario)

    result = verify(application, extraction)

    assert set(result) == RESULT_KEYS
    assert result["application_integrity_failure"] is True
    assert result["candidate_facts"] is None
    assert result["verified_evidence"] is None
    assert result["processing_errors"] == [{
        "error_type": "internal_processing_failure", "document": None,
        "fact_category": None, "processing_run_id": RUN,
    }]
    assert reasons_of(result)[-1] == (None, None, "application_integrity")
    assert validate_processing_errors(result["processing_errors"], RUN)["valid"] is True


@pytest.mark.parametrize("run", [None, "", "not-a-uuid", uuid.UUID(RUN)], ids=repr)
def test_invalid_authoritative_run_id_is_a_caller_error(scenario, run):
    with pytest.raises(ValueError):
        verify_duration_extraction(*scenario, run)


# --- Determinism and read-only behavior ----------------------------------------------


def test_verification_is_deterministic_apart_from_attempt_ids(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["start_line"] = 99

    first = verify_duration_extraction(application, extraction, RUN)
    second = verify_duration_extraction(application, extraction, RUN)

    for key in RESULT_KEYS - {"diagnostic_attempts"}:
        assert first[key] == second[key]
    strip = [{k: v for k, v in a.to_export_dict().items() if k != "attempt_id"} for a in first["diagnostic_attempts"]]
    assert strip == [{k: v for k, v in a.to_export_dict().items() if k != "attempt_id"}
                     for a in second["diagnostic_attempts"]]
    assert verify(application, extraction)["processing_errors"] == verify(application, extraction)["processing_errors"]


def test_inputs_are_not_modified(scenario):
    application, extraction = scenario
    extraction["duration_evidence"]["budget"][0]["start_line"] = 99
    application_before, extraction_before = copy.deepcopy(application), copy.deepcopy(extraction)

    result = verify(application, extraction)
    result["candidate_facts"][DURATION_CATEGORY]["budget_justification"]["value"] = 0
    result["verified_evidence"]["budget_justification"].clear()

    assert application == application_before
    assert extraction == extraction_before
