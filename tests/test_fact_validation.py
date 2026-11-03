"""Unit tests for the shared, read-only candidate-fact validator."""

from copy import deepcopy
from types import MappingProxyType

import pytest

from grantcheck.fact_schema import FACT_SOURCES, FACT_STATUSES, NOT_FOUND_REASON_CODES, SOURCE_RECORD_FIELDS
from grantcheck.fact_validation import (
    CandidateFactValidationError,
    validate_candidate_facts,
)


@pytest.fixture
def valid_facts():
    values = {
        "project_duration_months": 24,
        "personnel_role": "Graduate Research Assistant",
        "personnel_cost_usd": 24000,
        "personnel_effort_percent": 50,
        "travel_cost_usd": 8000,
        "travel_justification": "adequate",
    }
    return {
        category: {
            role: {"value": values[category], "status": "present", "evidence_text": "Source excerpt"}
            for role in roles
        }
        for category, roles in FACT_SOURCES.items()
    }


def changed(valid_facts, category, role, **updates):
    facts = deepcopy(valid_facts)
    facts[category][role].update(updates)
    return facts


def assert_invalid(facts, path):
    with pytest.raises(CandidateFactValidationError) as exc:
        validate_candidate_facts(facts)
    assert path in str(exc.value)


def test_valid_returns_none_without_mutation(valid_facts):
    before = deepcopy(valid_facts)
    assert validate_candidate_facts(valid_facts) is None
    assert valid_facts == before


def test_accepts_dict_subclasses(valid_facts):
    class SubDict(dict):
        pass

    facts = SubDict(valid_facts)
    category = next(iter(FACT_SOURCES))
    facts[category] = SubDict(facts[category])
    role = next(iter(FACT_SOURCES[category]))
    facts[category][role] = SubDict(facts[category][role])
    assert validate_candidate_facts(facts) is None


@pytest.mark.parametrize("bad", [None, [], (), "facts", MappingProxyType({})])
def test_rejects_nondict_root(bad):
    assert_invalid(bad, "candidate_facts")


@pytest.mark.parametrize("kind", ["missing", "extra", "wrong_type"])
def test_category_structure(valid_facts, kind):
    facts = deepcopy(valid_facts)
    category = next(iter(FACT_SOURCES))
    if kind == "missing":
        del facts[category]
    elif kind == "extra":
        facts["invented_fact"] = {}
        category = "invented_fact"
    else:
        facts[category] = []
    assert_invalid(facts, f"candidate_facts.{category}")


@pytest.mark.parametrize("kind", ["missing", "extra", "wrong_type"])
def test_role_structure(valid_facts, kind):
    facts = deepcopy(valid_facts)
    category = "personnel_cost_usd"
    role = next(iter(FACT_SOURCES[category]))
    if kind == "missing":
        del facts[category][role]
    elif kind == "extra":
        role = "invented_document"
        facts[category][role] = {}
    else:
        facts[category][role] = []
    assert_invalid(facts, f"candidate_facts.{category}.{role}")


@pytest.mark.parametrize("kind", ["missing", "extra"])
def test_record_fields(valid_facts, kind):
    facts = deepcopy(valid_facts)
    category, role = "personnel_cost_usd", FACT_SOURCES["personnel_cost_usd"][0]
    if kind == "missing":
        del facts[category][role]["value"]
        field = "value"
    else:
        facts[category][role]["confidence"] = 0.8
        field = "confidence"
    assert_invalid(facts, f"candidate_facts.{category}.{role}.{field}")


@pytest.mark.parametrize("status", ["missing", "uncertain", "unprocessable", "PRESENT", "", None, 1])
def test_invalid_or_nonpresent_status_value(valid_facts, status):
    category, role = "personnel_cost_usd", FACT_SOURCES["personnel_cost_usd"][0]
    facts = changed(valid_facts, category, role, status=status)
    field = "status" if status not in ("missing", "uncertain", "unprocessable") else "value"
    assert_invalid(facts, f"candidate_facts.{category}.{role}.{field}")


@pytest.mark.parametrize("status,evidence", [
    ("missing", None), ("unprocessable", None),
    ("uncertain", "Ambiguous source excerpt"),
])
def test_valid_nonpresent_records(valid_facts, status, evidence):
    category, role = "personnel_cost_usd", FACT_SOURCES["personnel_cost_usd"][0]
    facts = changed(valid_facts, category, role, status=status, value=None, evidence_text=evidence)
    assert validate_candidate_facts(facts) is None


@pytest.mark.parametrize("status,evidence", [
    ("missing", "text"), ("unprocessable", "text"),
    ("uncertain", None), ("uncertain", "  "),
    ("present", None), ("present", "  "),
])
def test_invalid_evidence(valid_facts, status, evidence):
    category, role = "personnel_cost_usd", FACT_SOURCES["personnel_cost_usd"][0]
    value = 24000 if status == "present" else None
    facts = changed(valid_facts, category, role, status=status, value=value, evidence_text=evidence)
    assert_invalid(facts, f"candidate_facts.{category}.{role}.evidence_text")


@pytest.mark.parametrize("category,bad", [
    ("project_duration_months", 0), ("project_duration_months", -1),
    ("project_duration_months", 24.0), ("project_duration_months", True),
    ("personnel_role", ""), ("personnel_role", "  "), ("personnel_role", 123),
    ("personnel_cost_usd", -0.01), ("personnel_cost_usd", float("nan")),
    ("personnel_cost_usd", float("inf")), ("personnel_cost_usd", True),
    ("personnel_effort_percent", -0.01), ("personnel_effort_percent", 100.01),
    ("personnel_effort_percent", float("-inf")),
    ("travel_cost_usd", -1), ("travel_cost_usd", False),
    ("travel_justification", "unsupported"), ("travel_justification", ""),
])
def test_invalid_present_values(valid_facts, category, bad):
    role = FACT_SOURCES[category][0]
    facts = changed(valid_facts, category, role, value=bad)
    assert_invalid(facts, f"candidate_facts.{category}.{role}.value")


@pytest.mark.parametrize("category,good", [
    ("project_duration_months", 1),
    ("personnel_cost_usd", 0), ("personnel_cost_usd", 24000.5),
    ("personnel_effort_percent", 0), ("personnel_effort_percent", 100.0),
    ("travel_cost_usd", 0.0), ("travel_justification", "adequate"),
    ("personnel_role", "  Researcher  "),
])
def test_valid_boundary_values(valid_facts, category, good):
    role = FACT_SOURCES[category][0]
    facts = changed(valid_facts, category, role, value=good)
    before = deepcopy(facts)
    assert validate_candidate_facts(facts) is None
    assert facts == before


# --- not_found (Increment 1A) ---------------------------------------------------


NOT_FOUND = {"value": None, "status": "not_found", "evidence_text": None}


def test_not_found_is_a_supported_status():
    assert "not_found" in FACT_STATUSES
    assert SOURCE_RECORD_FIELDS == ("value", "status", "evidence_text"), "records keep exactly three fields"


def test_not_found_reason_codes_are_reserved_separately():
    assert NOT_FOUND_REASON_CODES == ("unsupported_fact_type", "no_supported_pattern_matched")
    assert not set(NOT_FOUND_REASON_CODES) & set(SOURCE_RECORD_FIELDS)


@pytest.mark.parametrize(
    "category, role",
    [(category, role) for category, roles in FACT_SOURCES.items() for role in roles],
)
def test_well_formed_not_found_record_is_accepted(valid_facts, category, role):
    facts = deepcopy(valid_facts)
    facts[category][role] = dict(NOT_FOUND)
    before = deepcopy(facts)

    assert validate_candidate_facts(facts) is None
    assert facts == before


def test_all_records_not_found_is_accepted(valid_facts):
    facts = {category: {role: dict(NOT_FOUND) for role in roles} for category, roles in FACT_SOURCES.items()}
    assert validate_candidate_facts(facts) is None


@pytest.mark.parametrize("value", [24000, 0, "", "adequate", False, 0.0], ids=repr)
def test_not_found_with_value_is_rejected(valid_facts, value):
    facts = changed(valid_facts, "personnel_cost_usd", "budget", status="not_found", value=value, evidence_text=None)
    assert_invalid(facts, "candidate_facts.personnel_cost_usd.budget.value")


@pytest.mark.parametrize("evidence", ["Personnel Cost: $24,000", "", "   ", 0], ids=repr)
def test_not_found_with_evidence_is_rejected(valid_facts, evidence):
    facts = changed(valid_facts, "travel_justification", "budget_justification",
                    status="not_found", value=None, evidence_text=evidence)
    assert_invalid(facts, "candidate_facts.travel_justification.budget_justification.evidence_text")


@pytest.mark.parametrize("extra", ["reason", "reason_code", "not_found_reason"])
def test_reason_code_on_record_is_rejected(valid_facts, extra):
    facts = deepcopy(valid_facts)
    facts["personnel_role"]["budget"] = {**NOT_FOUND, extra: "no_supported_pattern_matched"}
    assert_invalid(facts, f"candidate_facts.personnel_role.budget.{extra}")
