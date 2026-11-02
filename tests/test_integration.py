
"""Integration tests for GrantCheck's offline document-review pipeline."""

import json
import pytest
from pathlib import Path

from grantcheck.document_intake import intake_application
from grantcheck.replay_loader import load_replay_facts
from grantcheck.rule_engine import evaluate_rules


SAMPLE_DATA_DIR = Path(__file__).resolve().parents[1] / "sample_data"


@pytest.mark.parametrize(
    "scenario_name",
    [
        "S01_consistent",
        "S02_personnel_cost_conflict",
        "S03_personnel_effort_conflict",
        "S04_duration_conflict",
        "S05_missing_travel_justification",
        "S06_ambiguous_travel_justification",
        "S07_unprocessable_document",
    ],
)
def test_scenario_application_pipeline(scenario_name):
    """Verify intake, replay loading, and rule outcomes for each scenario."""
    SCENARIO_DIR = SAMPLE_DATA_DIR / scenario_name
    expected = json.loads((SCENARIO_DIR / "expected_results.json").read_text(encoding="utf-8"))

    application = intake_application(
        expected["application_id"],
        SCENARIO_DIR / "budget.txt",
        SCENARIO_DIR / "budget_justification.txt",
        SCENARIO_DIR / "project_narrative.txt",
    )
    assert application["intake_status"] == "completed"
    assert application["intake_errors"] == []

    candidate_facts = load_replay_facts(application, SCENARIO_DIR / "replay_facts.json")

    for category, sources in expected["expected_normalized_values"].items():
        for role, expected_record in sources.items():
            actual_record = candidate_facts[category][role]
            assert actual_record["value"] == expected_record["value"]
            assert actual_record["status"] == expected_record["status"]

    results = evaluate_rules(candidate_facts)
    expected_outcomes = {
        rule_id.replace("-", ""): outcome
        for rule_id, outcome in expected["expected_rule_outcomes"].items()
    }
    assert set(results) == set(expected_outcomes)
    assert {rule_id: result["outcome"] for rule_id, result in results.items()} == expected_outcomes
    assert sum(result["outcome"] == "finding" for result in results.values()) == expected["expected_finding_count"]
