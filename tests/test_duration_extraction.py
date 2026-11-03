"""Tests for deterministic project-duration extraction.

Applications are built with real document intake from synthetic files written
as exact bytes, so LF and CRLF behavior is tested through actual intake.
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.document_intake import DOCUMENT_ROLES, intake_application  # noqa: E402
from grantcheck.duration_extraction import (  # noqa: E402
    DURATION_CATEGORY,
    NO_SUPPORTED_PATTERN_MATCHED,
    UNSUPPORTED_FACT_TYPE,
    extract_candidate_facts,
)
from grantcheck.fact_schema import FACT_SOURCES, NOT_FOUND_REASON_CODES, SOURCE_RECORD_FIELDS  # noqa: E402
from grantcheck.fact_validation import validate_candidate_facts  # noqa: E402
from grantcheck.provenance import resolve_provenance  # noqa: E402

FILENAMES = {"budget": "budget.txt", "budget_justification": "budget_justification.txt",
             "project_narrative": "project_narrative.txt"}
FILLER = "Synthetic document without a duration statement."
NOT_FOUND = {"value": None, "status": "not_found", "evidence_text": None}
OCCURRENCE_KEYS = {"document", "fact_category", "excerpt", "start_offset", "end_offset", "start_line", "match", "value"}


@pytest.fixture
def make_application(tmp_path):
    """Write synthetic documents (lists of lines) with an exact line ending, then run intake."""
    counter = iter(range(1000))

    def make(budget=(FILLER,), budget_justification=(FILLER,), project_narrative=(FILLER,), newline="\n"):
        folder = tmp_path / f"app{next(counter)}"
        folder.mkdir()
        paths = []
        for role, lines in zip(DOCUMENT_ROLES, (budget, budget_justification, project_narrative)):
            path = folder / FILENAMES[role]
            path.write_bytes((newline.join(lines) + newline).encode("utf-8"))
            paths.append(path)
        application = intake_application("GC-TEST", *paths)
        assert application["intake_status"] == "completed", "test setup: intake failed"
        return application
    return make


def duration(result, role="budget"):
    return result["candidate_facts"][DURATION_CATEGORY][role]


def extract_budget(make_application, *lines, newline="\n"):
    return extract_candidate_facts(make_application(budget=lines, newline=newline))


# --- Supported labels, units, normalization --------------------------------------


@pytest.mark.parametrize("line, months", [
    ("Project Duration: 24 months", 24),
    ("Duration: 24 months", 24),
    ("Duration: 2 years", 24),
    ("Duration: 3 years", 36),
    ("Duration: 1 year", 12),
    ("Duration: 1 month", 1),
    ("Project Duration: 18 Months", 18),       # unit capitalization is unambiguous
    ("Duration: 24 months.", 24),               # one final period
    ("Duration:   24   months", 24),            # extra spaces
    ("Duration:\t2\tyears", 24),                # tabs
    ("   Project Duration: 24 months", 24),     # indented label
], ids=lambda value: value if isinstance(value, str) else None)
def test_supported_statements_are_present(make_application, line, months):
    record = duration(extract_budget(make_application, line))
    assert record["status"] == "present"
    assert record["value"] == months
    assert isinstance(record["value"], int) and not isinstance(record["value"], bool)
    assert record["evidence_text"] == line.strip(" \t")


@pytest.mark.parametrize("line", [
    "Duration: approximately 2 years",
    "Duration: two years",
    "Duration: 2.5 years",
    "Duration: 0 months",
    "Duration: -12 months",
    "Duration: 24",
    "Duration: 24 weeks",
    "Duration: 24 mos",
    "Duration: 24months",
    "Duration: 024 months",
    "Duration: 24 months approx.",
    "Duration: 24-36 months",
    "Duration: 2 years 6 months",
    "Duration:",
], ids=lambda value: value)
def test_ambiguous_statements_are_uncertain(make_application, line):
    result = extract_budget(make_application, line)
    record = duration(result)
    assert record == {"value": None, "status": "uncertain", "evidence_text": line}
    assert result["duration_evidence"]["budget"][0]["match"] == "ambiguous"
    assert result["duration_evidence"]["budget"][0]["value"] is None


@pytest.mark.parametrize("line", [
    "Equipment Warranty Duration: 12 months",
    "Travel Duration: 3 months",
    "Duration of travel: 3 months",
    "Project Duration 24 months",           # no colon
    "duration: 24 months",                  # labels are case-sensitive
    "PROJECT DURATION: 24 months",
    "The Project Duration: 24 months is planned.",  # inside a sentence
    "The research will be conducted over two years.",
    "The proposed project will be conducted over a 24-month period.",
    "Duration : 24 months",                 # space before the colon
], ids=lambda value: value)
def test_unsupported_or_unrelated_text_is_not_found(make_application, line):
    result = extract_budget(make_application, line)
    assert duration(result) == NOT_FOUND
    assert result["not_found_reasons"][DURATION_CATEGORY]["budget"] == NO_SUPPORTED_PATTERN_MATCHED
    assert result["duration_evidence"]["budget"] == []


# --- Multiple statements ---------------------------------------------------------


def test_equivalent_statements_are_consolidated_with_every_occurrence(make_application):
    result = extract_budget(make_application, "Project Duration: 24 months", FILLER, "Duration: 2 years")
    assert duration(result) == {"value": 24, "status": "present", "evidence_text": "Project Duration: 24 months"}
    occurrences = result["duration_evidence"]["budget"]
    assert [(o["excerpt"], o["value"], o["match"]) for o in occurrences] == [
        ("Project Duration: 24 months", 24, "valid"),
        ("Duration: 2 years", 24, "valid"),
    ]


def test_conflicting_statements_are_uncertain_with_all_evidence(make_application):
    result = extract_budget(make_application, "Duration: 24 months", "Duration: 3 years")
    assert duration(result) == {"value": None, "status": "uncertain", "evidence_text": "Duration: 24 months"}
    assert [(o["value"], o["match"]) for o in result["duration_evidence"]["budget"]] == [(24, "valid"), (36, "valid")]


def test_valid_and_malformed_statements_are_uncertain(make_application):
    result = extract_budget(make_application, "Duration: 24 months", "Duration: about two years")
    # The record cites the statement that caused the ambiguity; both remain in the evidence.
    assert duration(result) == {"value": None, "status": "uncertain", "evidence_text": "Duration: about two years"}
    assert [o["match"] for o in result["duration_evidence"]["budget"]] == ["valid", "ambiguous"]


def test_identical_repeated_statements_keep_every_occurrence(make_application):
    result = extract_budget(make_application, "Duration: 24 months", "Duration: 24 months")
    assert duration(result)["status"] == "present"
    starts = [o["start_offset"] for o in result["duration_evidence"]["budget"]]
    assert len(starts) == 2 and starts[0] < starts[1]


# --- Evidence, offsets, and line endings -----------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_exact_excerpts_and_offsets(make_application, newline):
    lines = ["BUDGET", "", "  Project Duration: 24 months  ", "Duration: 2 years"]
    application = make_application(budget=lines, newline=newline)
    text = application["documents"]["budget"]["text"]

    result = extract_candidate_facts(application)
    first, second = result["duration_evidence"]["budget"]

    for occurrence in (first, second):
        assert set(occurrence) == OCCURRENCE_KEYS
        assert text[occurrence["start_offset"]:occurrence["end_offset"]] == occurrence["excerpt"]
        assert "\r" not in occurrence["excerpt"] and "\n" not in occurrence["excerpt"]
        assert (occurrence["document"], occurrence["fact_category"]) == ("budget", DURATION_CATEGORY)
    assert first["excerpt"] == "Project Duration: 24 months"   # indentation and trailing spaces excluded
    assert (first["start_line"], second["start_line"]) == (3, 4)
    assert first["start_offset"] == text.index("Project Duration:")
    assert second["start_offset"] == text.index("Duration: 2 years")


def test_lf_and_crlf_give_the_same_facts_and_different_offsets(make_application):
    lines = ["Header", "Project Duration: 36 months"]
    lf = extract_candidate_facts(make_application(budget=lines, newline="\n"))
    crlf = extract_candidate_facts(make_application(budget=lines, newline="\r\n"))

    assert lf["candidate_facts"] == crlf["candidate_facts"]
    assert crlf["duration_evidence"]["budget"][0]["start_offset"] == lf["duration_evidence"]["budget"][0]["start_offset"] + 1
    assert lf["duration_evidence"]["budget"][0]["start_line"] == crlf["duration_evidence"]["budget"][0]["start_line"] == 2


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_excerpts_resolve_through_existing_provenance(make_application, newline):
    application = make_application(
        budget=["Project Duration: 24 months"],
        budget_justification=["Duration: 2 years", "Duration: unknown"],
        newline=newline,
    )
    result = extract_candidate_facts(application)
    provenance = resolve_provenance(application, result["candidate_facts"])

    budget = provenance[DURATION_CATEGORY]["budget"]
    assert budget["occurrence_count"] == 1
    assert budget["locations"][0]["start_offset"] == result["duration_evidence"]["budget"][0]["start_offset"]
    assert provenance[DURATION_CATEGORY]["budget_justification"]["occurrence_count"] == 1  # "Duration: unknown"


# --- All 12 records and reason codes -----------------------------------------------


def test_twelve_records_in_schema_order_with_unsupported_categories_not_found(make_application):
    result = extract_candidate_facts(make_application(budget=["Project Duration: 24 months"]))
    facts = result["candidate_facts"]

    assert list(facts) == list(FACT_SOURCES)
    assert sum(len(roles) for roles in facts.values()) == 12
    for category, roles in FACT_SOURCES.items():
        assert list(facts[category]) == list(roles)
        for role in roles:
            assert tuple(facts[category][role]) == SOURCE_RECORD_FIELDS
            if category != DURATION_CATEGORY:
                assert facts[category][role] == NOT_FOUND
    validate_candidate_facts(facts)


def test_not_found_reasons_are_separate_and_cover_only_not_found_records(make_application):
    result = extract_candidate_facts(make_application(budget=["Project Duration: 24 months"]))
    reasons = result["not_found_reasons"]

    unsupported = {(c, r) for c, roles in reasons.items() for r, reason in roles.items() if reason == UNSUPPORTED_FACT_TYPE}
    no_match = {(c, r) for c, roles in reasons.items() for r, reason in roles.items() if reason == NO_SUPPORTED_PATTERN_MATCHED}
    assert len(unsupported) == 9 and all(c != DURATION_CATEGORY for c, _ in unsupported)
    assert no_match == {(DURATION_CATEGORY, "budget_justification"), (DURATION_CATEGORY, "project_narrative")}
    assert "budget" not in reasons.get(DURATION_CATEGORY, {}), "present records have no reason"
    assert {UNSUPPORTED_FACT_TYPE, NO_SUPPORTED_PATTERN_MATCHED} == set(NOT_FOUND_REASON_CODES)
    for roles in result["candidate_facts"].values():
        for record in roles.values():
            assert "reason" not in record


def test_every_extraction_outcome_passes_validation(make_application):
    application = make_application(
        budget=["Project Duration: 24 months"],
        budget_justification=["Duration: 24 months", "Duration: 3 years"],
        project_narrative=["Duration: roughly two years"],
    )
    result = extract_candidate_facts(application)
    statuses = [duration(result, role)["status"] for role in DOCUMENT_ROLES]
    assert statuses == ["present", "uncertain", "uncertain"]
    assert validate_candidate_facts(result["candidate_facts"]) is None


# --- Determinism, purity, and input contract ----------------------------------------


def test_extraction_is_deterministic_and_does_not_modify_the_application(make_application):
    application = make_application(budget=["Duration: 2 years", "Duration: 24 months"])
    before = copy.deepcopy(application)

    first = extract_candidate_facts(application)
    second = extract_candidate_facts(application)

    assert first == second
    assert application == before
    first["candidate_facts"][DURATION_CATEGORY]["budget"]["value"] = 0
    assert extract_candidate_facts(application) == second, "results share no state between calls"


@pytest.mark.parametrize("application", [
    None, [], {}, {"documents": []},
    {"documents": {"budget": {"text": "x"}, "budget_justification": {"text": "x"}}},
    {"documents": {role: None for role in DOCUMENT_ROLES}},
    {"documents": {role: {"text": b"bytes"} for role in DOCUMENT_ROLES}},
], ids=["none", "list", "empty", "documents_list", "missing_role", "failed_documents", "bytes_text"])
def test_invalid_application_is_rejected(application):
    with pytest.raises(ValueError):
        extract_candidate_facts(application)


def test_synthetic_scenario_documents(make_application):
    # The S01 sample documents state the duration in three ways; only the budget's labelled line is structured.
    scenario = ROOT / "sample_data" / "S01_consistent"
    application = intake_application("GC-S01", *(scenario / FILENAMES[role] for role in DOCUMENT_ROLES))

    result = extract_candidate_facts(application)

    assert duration(result, "budget") == {"value": 24, "status": "present", "evidence_text": "Project Duration: 24 months"}
    assert duration(result, "budget_justification") == NOT_FOUND   # "...over a 24-month period."
    assert duration(result, "project_narrative") == NOT_FOUND      # "...over two years"
