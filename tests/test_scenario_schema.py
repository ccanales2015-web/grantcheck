"""Schema tests for the real GrantCheck sample scenarios.

Each folder under sample_data/ named like "S01_consistent" holds one
synthetic grant application plus an expected_results.json file describing
what GrantCheck should produce for it. These tests check that every real
expected_results.json follows the approved schema, so the fixtures stay
trustworthy before any application code relies on them.

The schema rules themselves live in scenario_loader.py (one shared validator).
Each test below runs one section of that validator per scenario, so a failure
points at both the fixture and the part of the schema it breaks.
"""

from pathlib import Path

import pytest

import scenario_loader
from scenario_loader import (
    discover_scenario_dirs,
    folder_scenario_id,
    load_scenario,
    read_expected_results,
)

# Resolve paths from this file's location, not the terminal's working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"

SCENARIO_DIRS = discover_scenario_dirs(SAMPLE_DATA_DIR)


def where(scenario_dir):
    return f"{scenario_dir.name}/{scenario_loader.EXPECTED_RESULTS_FILE}"


def load_by_id(scenario_id):
    """Find and load a real scenario by its ID (e.g. "S06") for the targeted tests."""
    for scenario_dir in SCENARIO_DIRS:
        if folder_scenario_id(scenario_dir) == scenario_id:
            return load_scenario(scenario_dir)["expected_results"]
    pytest.fail(f"Scenario {scenario_id} not found under {SAMPLE_DATA_DIR}")


# Parametrizing over the discovered folders gives one test per scenario,
# named after the folder, so a failure points straight at the bad fixture.
scenario_params = pytest.mark.parametrize(
    "scenario_dir", SCENARIO_DIRS, ids=[path.name for path in SCENARIO_DIRS]
)


def test_scenarios_are_discovered():
    assert SCENARIO_DIRS, f"No scenario folders (S01_...) found under {SAMPLE_DATA_DIR}"


@scenario_params
def test_top_level_keys(scenario_dir):
    # read_expected_results also checks the file exists and is valid JSON.
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_top_level(data, where(scenario_dir))


@scenario_params
def test_ids_match_folder(scenario_dir):
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_identity(data, where(scenario_dir), folder_scenario_id(scenario_dir))


@scenario_params
def test_fact_categories_and_documents(scenario_dir):
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_fact_categories(data, where(scenario_dir))


@scenario_params
def test_fact_entries(scenario_dir):
    # Value/status pairs, approved statuses, null rules, value types,
    # and the travel_justification vocabulary.
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_fact_entries(data, where(scenario_dir))


@scenario_params
def test_rule_outcomes(scenario_dir):
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_rule_outcomes(data, where(scenario_dir))


@scenario_params
def test_status_flags_and_counts(scenario_dir):
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_status_fields(data, where(scenario_dir))


@scenario_params
def test_outcomes_are_consistent(scenario_dir):
    data = read_expected_results(scenario_dir)
    scenario_loader.validate_consistency(data, where(scenario_dir))


def test_s06_ambiguous_travel_justification():
    data = load_by_id("S06")
    assert data["expected_finding_count"] == 0, "S06: expected zero findings"
    assert data["expected_rule_outcomes"]["R-04"] == "uncertain", "S06: R-04 should be 'uncertain'"
    assert data["expected_review_required"] is True, "S06: review should be required"


def test_s07_unprocessable_document():
    data = load_by_id("S07")
    assert data["expected_processing_status"] == "exception", "S07: status should be 'exception'"
    assert set(data["expected_rule_outcomes"].values()) == {"not_evaluated"}, (
        f"S07: all rules should be 'not_evaluated', got {data['expected_rule_outcomes']}"
    )
    for fact_name, documents in data["expected_normalized_values"].items():
        entry = documents["budget_justification"]
        assert entry == {"value": None, "status": "unprocessable"}, (
            f"S07: {fact_name}.budget_justification should be null/unprocessable, got {entry!r}"
        )
