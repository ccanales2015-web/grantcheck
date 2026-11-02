"""Unit tests for the shared, read-only candidate-fact validator."""

from copy import deepcopy
from types import MappingProxyType

import pytest

from grantcheck.fact_schema import FACT_SOURCES
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
