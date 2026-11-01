"""Schema tests for the GrantCheck sample scenarios.

Each folder under sample_data/ named like "S01_consistent" holds one
synthetic grant application plus an expected_results.json file describing
what GrantCheck should produce for it. These tests check that every
expected_results.json follows the approved schema, so the fixtures stay
trustworthy before any application code relies on them.
"""

import json
import re
from pathlib import Path

import pytest

# Resolve paths from this file's location, not the terminal's working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"

# Scenario folders look like "S01_consistent": "S", two digits, underscore, name.
SCENARIO_DIR_PATTERN = re.compile(r"^(S\d{2})_.+$")
APPLICATION_ID_PATTERN = re.compile(r"^GC-S\d{2}$")

EXPECTED_TOP_LEVEL_KEYS = {
    "scenario_id",
    "application_id",
    "scenario_name",
    "expected_rule_outcomes",
    "expected_finding_count",
    "expected_processing_status",
    "expected_review_required",
    "expected_normalized_values",
}

# Each fact category and the source documents it is compared across.
EXPECTED_FACT_DOCUMENTS = {
    "project_duration_months": {"budget", "budget_justification", "project_narrative"},
    "personnel_role": {"budget", "budget_justification"},
    "personnel_cost_usd": {"budget", "budget_justification"},
    "personnel_effort_percent": {"budget", "budget_justification"},
    "travel_cost_usd": {"budget", "budget_justification"},
    "travel_justification": {"budget_justification"},
}

NUMERIC_FACTS = {
    "project_duration_months",
    "personnel_cost_usd",
    "personnel_effort_percent",
    "travel_cost_usd",
}
TEXT_FACTS = {"personnel_role"}
TRAVEL_JUSTIFICATION_VALUES = {"adequate"}

ALLOWED_FACT_STATUSES = {"present", "missing", "uncertain", "unprocessable"}
EXPECTED_RULE_IDS = {"R-01", "R-02", "R-03", "R-04"}
ALLOWED_RULE_OUTCOMES = {"pass", "finding", "uncertain", "not_evaluated"}
ALLOWED_PROCESSING_STATUSES = {"completed", "exception"}


def discover_scenario_dirs():
    """Return scenario folders sorted by name so test order is deterministic."""
    if not SAMPLE_DATA_DIR.is_dir():
        return []
    return sorted(
        (path for path in SAMPLE_DATA_DIR.iterdir()
         if path.is_dir() and SCENARIO_DIR_PATTERN.match(path.name)),
        key=lambda path: path.name,
    )


SCENARIO_DIRS = discover_scenario_dirs()


def load_expected_results(scenario_dir):
    """Load a scenario's expected_results.json, failing with a clear message."""
    results_path = scenario_dir / "expected_results.json"
    assert results_path.is_file(), f"{scenario_dir.name}: missing expected_results.json"
    try:
        return json.loads(results_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        pytest.fail(f"{scenario_dir.name}: expected_results.json is not valid JSON ({error})")


def is_number(value):
    """True for int/float but not bool (in Python, True and False are ints)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load_scenario(scenario_id):
    """Find and load a scenario by its ID (e.g. "S06") for the targeted tests."""
    for scenario_dir in SCENARIO_DIRS:
        if SCENARIO_DIR_PATTERN.match(scenario_dir.name).group(1) == scenario_id:
            return load_expected_results(scenario_dir)
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
    data = load_expected_results(scenario_dir)
    assert isinstance(data, dict), f"{scenario_dir.name}: top level must be a JSON object"
    actual_keys = set(data)
    assert actual_keys == EXPECTED_TOP_LEVEL_KEYS, (
        f"{scenario_dir.name}: unexpected top-level keys. "
        f"Missing: {sorted(EXPECTED_TOP_LEVEL_KEYS - actual_keys)}, "
        f"extra: {sorted(actual_keys - EXPECTED_TOP_LEVEL_KEYS)}"
    )


@scenario_params
def test_ids_match_folder(scenario_dir):
    data = load_expected_results(scenario_dir)
    folder_id = SCENARIO_DIR_PATTERN.match(scenario_dir.name).group(1)
    assert data["scenario_id"] == folder_id, (
        f"{scenario_dir.name}: scenario_id is {data['scenario_id']!r}, expected {folder_id!r}"
    )
    application_id = data["application_id"]
    assert isinstance(application_id, str) and APPLICATION_ID_PATTERN.match(application_id), (
        f"{scenario_dir.name}: application_id {application_id!r} does not match GC-Sxx"
    )
    assert application_id == f"GC-{folder_id}", (
        f"{scenario_dir.name}: application_id {application_id!r}, expected 'GC-{folder_id}'"
    )
    assert isinstance(data["scenario_name"], str) and data["scenario_name"].strip(), (
        f"{scenario_dir.name}: scenario_name must be a non-empty string"
    )


@scenario_params
def test_fact_categories_and_documents(scenario_dir):
    facts = load_expected_results(scenario_dir)["expected_normalized_values"]
    assert set(facts) == set(EXPECTED_FACT_DOCUMENTS), (
        f"{scenario_dir.name}: fact categories are {sorted(facts)}, "
        f"expected {sorted(EXPECTED_FACT_DOCUMENTS)}"
    )
    for fact_name, expected_documents in EXPECTED_FACT_DOCUMENTS.items():
        assert set(facts[fact_name]) == expected_documents, (
            f"{scenario_dir.name}: {fact_name} has documents {sorted(facts[fact_name])}, "
            f"expected {sorted(expected_documents)}"
        )


@scenario_params
def test_fact_entries(scenario_dir):
    facts = load_expected_results(scenario_dir)["expected_normalized_values"]
    for fact_name, documents in facts.items():
        for document_name, entry in documents.items():
            where = f"{scenario_dir.name}: {fact_name}.{document_name}"
            assert isinstance(entry, dict) and set(entry) == {"value", "status"}, (
                f"{where} must have exactly 'value' and 'status', got {entry!r}"
            )
            status, value = entry["status"], entry["value"]
            assert status in ALLOWED_FACT_STATUSES, (
                f"{where}: status {status!r} not in {sorted(ALLOWED_FACT_STATUSES)}"
            )

            if status != "present":
                assert value is None, f"{where}: status is {status!r}, so value must be null"
                continue

            assert value is not None, f"{where}: status is 'present', so value must not be null"
            if fact_name in NUMERIC_FACTS:
                assert is_number(value), f"{where}: expected a number, got {value!r}"
            elif fact_name in TEXT_FACTS:
                assert isinstance(value, str) and value.strip(), (
                    f"{where}: expected a non-empty string, got {value!r}"
                )
            elif fact_name == "travel_justification":
                assert value in TRAVEL_JUSTIFICATION_VALUES, (
                    f"{where}: value {value!r} not in {sorted(TRAVEL_JUSTIFICATION_VALUES)}"
                )


@scenario_params
def test_rule_outcomes(scenario_dir):
    outcomes = load_expected_results(scenario_dir)["expected_rule_outcomes"]
    assert set(outcomes) == EXPECTED_RULE_IDS, (
        f"{scenario_dir.name}: rule IDs are {sorted(outcomes)}, expected {sorted(EXPECTED_RULE_IDS)}"
    )
    for rule_id, outcome in outcomes.items():
        assert outcome in ALLOWED_RULE_OUTCOMES, (
            f"{scenario_dir.name}: {rule_id} outcome {outcome!r} not in {sorted(ALLOWED_RULE_OUTCOMES)}"
        )


@scenario_params
def test_status_flags_and_counts(scenario_dir):
    data = load_expected_results(scenario_dir)
    name = scenario_dir.name
    assert data["expected_processing_status"] in ALLOWED_PROCESSING_STATUSES, (
        f"{name}: processing status {data['expected_processing_status']!r} "
        f"not in {sorted(ALLOWED_PROCESSING_STATUSES)}"
    )
    assert isinstance(data["expected_review_required"], bool), (
        f"{name}: expected_review_required must be true or false, "
        f"got {data['expected_review_required']!r}"
    )
    assert is_nonnegative_int(data["expected_finding_count"]), (
        f"{name}: expected_finding_count must be a nonnegative integer, "
        f"got {data['expected_finding_count']!r}"
    )


@scenario_params
def test_outcomes_are_consistent(scenario_dir):
    data = load_expected_results(scenario_dir)
    name = scenario_dir.name
    outcomes = list(data["expected_rule_outcomes"].values())
    review_required = data["expected_review_required"]

    # Current test convention: one finding per rule whose outcome is "finding".
    finding_outcomes = outcomes.count("finding")
    assert data["expected_finding_count"] == finding_outcomes, (
        f"{name}: expected_finding_count is {data['expected_finding_count']}, "
        f"but {finding_outcomes} rule(s) have outcome 'finding'"
    )
    if "finding" in outcomes or "uncertain" in outcomes:
        assert review_required is True, (
            f"{name}: a 'finding' or 'uncertain' outcome requires expected_review_required = true"
        )
    if data["expected_processing_status"] == "exception":
        assert review_required is True, (
            f"{name}: processing status 'exception' requires expected_review_required = true"
        )


def test_s06_ambiguous_travel_justification():
    data = load_scenario("S06")
    assert data["expected_finding_count"] == 0, "S06: expected zero findings"
    assert data["expected_rule_outcomes"]["R-04"] == "uncertain", "S06: R-04 should be 'uncertain'"
    assert data["expected_review_required"] is True, "S06: review should be required"


def test_s07_unprocessable_document():
    data = load_scenario("S07")
    assert data["expected_processing_status"] == "exception", "S07: status should be 'exception'"
    assert set(data["expected_rule_outcomes"].values()) == {"not_evaluated"}, (
        f"S07: all rules should be 'not_evaluated', got {data['expected_rule_outcomes']}"
    )
    for fact_name, documents in data["expected_normalized_values"].items():
        entry = documents["budget_justification"]
        assert entry == {"value": None, "status": "unprocessable"}, (
            f"S07: {fact_name}.budget_justification should be null/unprocessable, got {entry!r}"
        )
