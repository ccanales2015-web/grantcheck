"""Offline, deterministic tests for the four GrantCheck review rules."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.fact_validation import CandidateFactValidationError  # noqa: E402
from grantcheck.rule_engine import evaluate_rules  # noqa: E402


@pytest.fixture
def facts():
    return json.loads((ROOT / "sample_data" / "S01_consistent" / "replay_facts.json").read_text(encoding="utf-8"))["candidate_facts"]


def change(facts, category, role, *, status="present", value=None):
    record = facts[category][role]
    record["status"] = status
    if status == "present":
        record["value"] = value
        record["evidence_text"] = "Test excerpt"
    else:
        record["value"] = None
        record["evidence_text"] = "Test excerpt" if status == "uncertain" else None


def test_baseline_all_pass(facts):
    results = evaluate_rules(facts)
    assert list(results) == ["R01", "R02", "R03", "R04"]
    assert [result["outcome"] for result in results.values()] == ["pass"] * 4
    assert all(set(result) == {"rule_name", "outcome", "message", "evidence"} for result in results.values())


@pytest.mark.parametrize("rule,category,role,value", [
    ("R01", "personnel_cost_usd", "budget_justification", 20000),
    ("R02", "personnel_effort_percent", "budget_justification", 25),
    ("R03", "project_duration_months", "project_narrative", 36),
])
def test_mismatches(facts, rule, category, role, value):
    change(facts, category, role, value=value)
    assert evaluate_rules(facts)[rule]["outcome"] == "finding"


@pytest.mark.parametrize("status,expected", [
    ("missing", "not_evaluated"), ("unprocessable", "not_evaluated"),
    ("uncertain", "uncertain"),
])
def test_consistency_status(facts, status, expected):
    change(facts, "personnel_cost_usd", "budget_justification", status=status)
    assert evaluate_rules(facts)["R01"]["outcome"] == expected


def test_missing_overrides_uncertain(facts):
    change(facts, "project_duration_months", "budget", status="uncertain")
    change(facts, "project_duration_months", "project_narrative", status="missing")
    assert evaluate_rules(facts)["R03"]["outcome"] == "not_evaluated"


@pytest.mark.parametrize("cost_status,cost_value,just_status,expected", [
    ("present", 0, "missing", "pass"),
    ("present", 8000, "missing", "finding"),
    ("present", 8000, "uncertain", "uncertain"),
    ("present", 8000, "unprocessable", "not_evaluated"),
    ("present", 8000, "present", "pass"),
    ("missing", None, "present", "not_evaluated"),
    ("uncertain", None, "present", "uncertain"),
    ("unprocessable", None, "present", "not_evaluated"),
])
def test_travel_outcomes(facts, cost_status, cost_value, just_status, expected):
    change(facts, "travel_cost_usd", "budget", status=cost_status, value=cost_value)
    change(facts, "travel_justification", "budget_justification", status=just_status,
           value="adequate" if just_status == "present" else None)
    assert evaluate_rules(facts)["R04"]["outcome"] == expected


def test_does_not_mutate_and_evidence_is_detached(facts):
    before = copy.deepcopy(facts)
    result = evaluate_rules(facts)
    assert facts == before
    result["R01"]["evidence"]["personnel_cost_usd"]["budget"]["value"] = 0
    assert facts == before


def test_invalid_input_is_atomic(facts):
    facts["project_duration_months"]["budget"]["value"] = 0
    with pytest.raises(CandidateFactValidationError):
        evaluate_rules(facts)


@pytest.mark.parametrize("scenario_prefix,expected", [
    ("S01_", ("pass", "pass", "pass", "pass")),
    ("S02_", ("finding", "pass", "pass", "pass")),
    ("S03_", ("pass", "finding", "pass", "pass")),
    ("S04_", ("pass", "pass", "finding", "pass")),
    ("S05_", ("pass", "pass", "pass", "finding")),
    ("S06_", ("pass", "pass", "pass", "uncertain")),
    ("S07_", ("not_evaluated", "not_evaluated", "not_evaluated", "not_evaluated")),
])
def test_all_scenario_replays(scenario_prefix, expected):
    matches = list((ROOT / "sample_data").glob(f"{scenario_prefix}*/replay_facts.json"))
    assert len(matches) == 1, f"Expected one replay fixture for {scenario_prefix}"
    facts = json.loads(matches[0].read_text(encoding="utf-8"))["candidate_facts"]
    results = evaluate_rules(facts)
    assert tuple(result["outcome"] for result in results.values()) == expected
