"""Tests for scenario_loader.py.

The real sample_data/ folders are only ever read. Every broken fixture is
built in pytest's tmp_path by copying the real S01 scenario and then changing
one thing, so each test shows exactly which mistake the loader must catch.
"""

import json
import shutil
from pathlib import Path

import pytest

from scenario_loader import (
    EXPECTED_RESULTS_FILE,
    SOURCE_DOCUMENT_FILES,
    ScenarioValidationError,
    load_all_scenarios,
    load_scenario,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"
BASELINE_SCENARIO = SAMPLE_DATA_DIR / "S01_consistent"

REAL_SCENARIO_IDS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07"]

DELETE = object()  # marker meaning "remove this field" in the mutation table


# --- Helpers -----------------------------------------------------------------


def make_scenario(parent, folder_name="S01_fixture"):
    """Copy the real S01 scenario into parent/folder_name with matching IDs."""
    scenario_dir = parent / folder_name
    shutil.copytree(BASELINE_SCENARIO, scenario_dir)
    scenario_id = folder_name[:3]
    data = read_json(scenario_dir)
    data["scenario_id"] = scenario_id
    data["application_id"] = f"GC-{scenario_id}"
    write_json(scenario_dir, data)
    return scenario_dir


def read_json(scenario_dir):
    return json.loads((scenario_dir / EXPECTED_RESULTS_FILE).read_text(encoding="utf-8"))


def write_json(scenario_dir, data):
    (scenario_dir / EXPECTED_RESULTS_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def apply_change(data, dotted_path, new_value):
    """Set (or delete, with DELETE) a nested field such as "expected_rule_outcomes.R-01"."""
    *parents, last = dotted_path.split(".")
    target = data
    for key in parents:
        target = target[key]
    if new_value is DELETE:
        del target[last]
    else:
        target[last] = new_value


def assert_error_mentions(error, *fragments):
    message = str(error.value)
    for fragment in fragments:
        assert fragment in message, f"expected {fragment!r} in error message: {message}"


# --- Loading the real scenarios ---------------------------------------------


def test_load_all_real_scenarios_in_order():
    scenarios = load_all_scenarios(SAMPLE_DATA_DIR)
    assert [s["scenario_id"] for s in scenarios] == REAL_SCENARIO_IDS


def test_load_all_is_deterministic():
    first = [s["folder_name"] for s in load_all_scenarios(SAMPLE_DATA_DIR)]
    second = [s["folder_name"] for s in load_all_scenarios(SAMPLE_DATA_DIR)]
    assert first == second == sorted(first)


def test_load_single_scenario_returns_plain_dict():
    scenario = load_scenario(BASELINE_SCENARIO)

    assert type(scenario) is dict
    assert set(scenario) == {"scenario_id", "folder_name", "path", "documents", "expected_results"}
    assert scenario["scenario_id"] == "S01"
    assert scenario["folder_name"] == "S01_consistent"
    assert type(scenario["documents"]) is dict
    assert set(scenario["documents"]) == set(SOURCE_DOCUMENT_FILES)
    assert all(isinstance(text, str) for text in scenario["documents"].values())
    assert type(scenario["expected_results"]) is dict


def test_loaded_documents_contain_source_text():
    documents = load_scenario(BASELINE_SCENARIO)["documents"]
    assert "Personnel Cost: $24,000" in documents["budget"]
    assert "24-month period" in documents["budget_justification"]
    assert "two years" in documents["project_narrative"]


def test_s06_loads_with_vague_travel_justification():
    scenario = load_scenario(SAMPLE_DATA_DIR / "S06_ambiguous_travel_justification")
    expected = scenario["expected_results"]

    assert "attendance and transportation activities" in scenario["documents"]["budget_justification"]
    assert expected["expected_rule_outcomes"]["R-04"] == "uncertain"
    assert expected["expected_finding_count"] == 0
    assert expected["expected_review_required"] is True
    assert expected["expected_processing_status"] == "completed"


def test_s07_placeholder_is_loaded_as_ordinary_text():
    # The loader must not treat S07's placeholder as a real extraction failure:
    # it loads successfully, and the placeholder comes back verbatim.
    scenario = load_scenario(SAMPLE_DATA_DIR / "S07_unprocessable_document")
    documents = scenario["documents"]
    expected = scenario["expected_results"]

    assert documents["budget_justification"].startswith("[UNREADABLE DOCUMENT CONTENT]")
    assert "Personnel Cost: $24,000" in documents["budget"]
    assert "two years" in documents["project_narrative"]
    assert expected["expected_processing_status"] == "exception"
    assert set(expected["expected_rule_outcomes"].values()) == {"not_evaluated"}


# --- Discovery ---------------------------------------------------------------


def test_discovery_sorts_and_ignores_unrelated_entries(tmp_path):
    for name in ["S03_third", "S01_first", "S02_second"]:
        make_scenario(tmp_path, name)
    (tmp_path / "notes").mkdir()
    (tmp_path / "S1_missing_digit").mkdir()
    (tmp_path / "s04_lowercase").mkdir()
    (tmp_path / "S05_a_file.txt").write_text("not a folder", encoding="utf-8")

    scenarios = load_all_scenarios(tmp_path)

    assert [s["folder_name"] for s in scenarios] == ["S01_first", "S02_second", "S03_third"]


def test_no_scenarios_found(tmp_path):
    (tmp_path / "unrelated").mkdir()
    with pytest.raises(ScenarioValidationError) as error:
        load_all_scenarios(tmp_path)
    assert_error_mentions(error, "No scenario folders", str(tmp_path))


def test_missing_sample_data_folder(tmp_path):
    with pytest.raises(ScenarioValidationError) as error:
        load_all_scenarios(tmp_path / "does_not_exist")
    assert_error_mentions(error, "Sample data folder not found")


def test_load_scenario_rejects_badly_named_folder(tmp_path):
    folder = tmp_path / "consistent"
    folder.mkdir()
    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(folder)
    assert_error_mentions(error, "consistent", "not a scenario folder")


def test_batch_load_names_the_broken_scenario(tmp_path):
    make_scenario(tmp_path, "S01_good")
    broken = make_scenario(tmp_path, "S02_broken")
    (broken / "budget.txt").unlink()

    with pytest.raises(ScenarioValidationError) as error:
        load_all_scenarios(tmp_path)
    assert_error_mentions(error, "S02_broken/budget.txt", "missing")


# --- Missing files and malformed JSON ----------------------------------------


@pytest.mark.parametrize(
    "file_name", [*SOURCE_DOCUMENT_FILES.values(), EXPECTED_RESULTS_FILE]
)
def test_missing_required_file(tmp_path, file_name):
    scenario_dir = make_scenario(tmp_path)
    (scenario_dir / file_name).unlink()

    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(scenario_dir)
    assert_error_mentions(error, f"S01_fixture/{file_name}", "required file is missing")


def test_malformed_json(tmp_path):
    scenario_dir = make_scenario(tmp_path)
    (scenario_dir / EXPECTED_RESULTS_FILE).write_text('{"scenario_id": "S01",', encoding="utf-8")

    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(scenario_dir)
    assert_error_mentions(error, "S01_fixture/expected_results.json", "not valid JSON")


def test_top_level_must_be_object(tmp_path):
    scenario_dir = make_scenario(tmp_path)
    write_json(scenario_dir, ["not", "an", "object"])

    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(scenario_dir)
    assert_error_mentions(error, "S01_fixture/expected_results.json", "<top level>", "JSON object")


# --- Schema violations -------------------------------------------------------
# Each case: (test id, list of (field path, new value), fragments the error must contain).
# The baseline is S01, which has all "pass" outcomes, zero findings, review false.

FACTS = "expected_normalized_values"

INVALID_FIXTURE_CASES = [
    # Top-level structure
    ("extra_top_level_key", [("notes", "x")], ["<top level>", "extra: ['notes']"]),
    ("missing_top_level_key", [("expected_review_required", DELETE)],
     ["<top level>", "Missing: ['expected_review_required']"]),
    # Identity
    ("scenario_id_mismatch", [("scenario_id", "S09")], ["scenario_id", "folder name says 'S01'"]),
    ("application_id_mismatch", [("application_id", "GC-S02")], ["application_id", "expected 'GC-S01'"]),
    ("application_id_bad_format", [("application_id", "GC-1")], ["application_id", "does not match GC-Sxx"]),
    ("empty_scenario_name", [("scenario_name", "  ")], ["scenario_name", "non-empty string"]),
    # Fact categories and document keys
    ("extra_fact_category", [(f"{FACTS}.travel_purpose", {})], [FACTS, "extra: ['travel_purpose']"]),
    ("missing_fact_category", [(f"{FACTS}.travel_justification", DELETE)],
     [FACTS, "Missing: ['travel_justification']"]),
    ("missing_document_key", [(f"{FACTS}.project_duration_months.project_narrative", DELETE)],
     [f"{FACTS}.project_duration_months", "Missing: ['project_narrative']"]),
    ("extra_document_key", [(f"{FACTS}.travel_justification.budget", {"value": None, "status": "missing"})],
     [f"{FACTS}.travel_justification", "extra: ['budget']"]),
    # Value/status entries
    ("extra_entry_field", [(f"{FACTS}.personnel_role.budget.note", "x")],
     [f"{FACTS}.personnel_role.budget", "extra: ['note']"]),
    ("missing_entry_field", [(f"{FACTS}.personnel_role.budget.status", DELETE)],
     [f"{FACTS}.personnel_role.budget", "Missing: ['status']"]),
    ("invalid_status", [(f"{FACTS}.travel_cost_usd.budget.status", "absent")],
     [f"{FACTS}.travel_cost_usd.budget.status", "'absent' is not one of"]),
    ("present_with_null_value", [(f"{FACTS}.personnel_cost_usd.budget.value", None)],
     [f"{FACTS}.personnel_cost_usd.budget.value", "must not be null"]),
    ("missing_with_value", [(f"{FACTS}.travel_cost_usd.budget_justification.status", "missing")],
     [f"{FACTS}.travel_cost_usd.budget_justification.value", "must be null when status is 'missing'"]),
    ("unprocessable_with_value", [(f"{FACTS}.personnel_role.budget_justification.status", "unprocessable")],
     [f"{FACTS}.personnel_role.budget_justification.value", "must be null when status is 'unprocessable'"]),
    ("boolean_as_number", [(f"{FACTS}.personnel_effort_percent.budget.value", True)],
     [f"{FACTS}.personnel_effort_percent.budget.value", "must be a number"]),
    ("string_as_number", [(f"{FACTS}.travel_cost_usd.budget.value", "8000")],
     [f"{FACTS}.travel_cost_usd.budget.value", "must be a number"]),
    ("blank_role", [(f"{FACTS}.personnel_role.budget.value", "  ")],
     [f"{FACTS}.personnel_role.budget.value", "non-empty string"]),
    ("unapproved_travel_justification", [(f"{FACTS}.travel_justification.budget_justification.value", "good")],
     [f"{FACTS}.travel_justification.budget_justification.value", "'good' is not one of"]),
    # Rule outcomes
    ("invalid_rule_outcome", [("expected_rule_outcomes.R-02", "fail")],
     ["expected_rule_outcomes.R-02", "'fail' is not one of"]),
    ("missing_rule", [("expected_rule_outcomes.R-04", DELETE)],
     ["expected_rule_outcomes", "Missing: ['R-04']"]),
    ("extra_rule", [("expected_rule_outcomes.R-05", "pass")],
     ["expected_rule_outcomes", "extra: ['R-05']"]),
    # Status fields
    ("invalid_processing_status", [("expected_processing_status", "done")],
     ["expected_processing_status", "'done' is not one of"]),
    ("review_required_as_string", [("expected_review_required", "true")],
     ["expected_review_required", "must be true or false"]),
    ("finding_count_as_boolean", [("expected_finding_count", False)],
     ["expected_finding_count", "nonnegative integer"]),
    ("negative_finding_count", [("expected_finding_count", -1)],
     ["expected_finding_count", "nonnegative integer"]),
    # Consistency invariants
    ("finding_count_too_high", [("expected_finding_count", 1)],
     ["expected_finding_count", "is 1, but 0 rule(s)"]),
    ("finding_count_too_low", [("expected_rule_outcomes.R-01", "finding"), ("expected_review_required", True)],
     ["expected_finding_count", "is 0, but 1 rule(s)"]),
    ("finding_without_review", [("expected_rule_outcomes.R-01", "finding"), ("expected_finding_count", 1)],
     ["expected_review_required", "'finding' or 'uncertain'"]),
    ("uncertain_without_review", [("expected_rule_outcomes.R-04", "uncertain")],
     ["expected_review_required", "'finding' or 'uncertain'"]),
    ("exception_without_review", [
        ("expected_processing_status", "exception"),
        *[(f"expected_rule_outcomes.R-0{n}", "not_evaluated") for n in range(1, 5)],
    ], ["expected_review_required", "'exception'"]),
]


@pytest.mark.parametrize(
    "changes, fragments",
    [(changes, fragments) for _, changes, fragments in INVALID_FIXTURE_CASES],
    ids=[case_id for case_id, _, _ in INVALID_FIXTURE_CASES],
)
def test_invalid_fixture_is_rejected(tmp_path, changes, fragments):
    scenario_dir = make_scenario(tmp_path)
    data = read_json(scenario_dir)
    for dotted_path, new_value in changes:
        apply_change(data, dotted_path, new_value)
    write_json(scenario_dir, data)

    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(scenario_dir)
    # Every message must name the scenario folder, the file, and the field.
    assert_error_mentions(error, "S01_fixture/expected_results.json", *fragments)


def test_unmodified_copy_is_valid(tmp_path):
    # Guards the table above: each case fails only because of its own change.
    scenario = load_scenario(make_scenario(tmp_path))
    assert scenario["scenario_id"] == "S01"


def test_not_found_expected_fact_status_is_recognized(tmp_path):
    scenario_dir = make_scenario(tmp_path)
    data = read_json(scenario_dir)
    apply_change(data, f"{FACTS}.project_duration_months.project_narrative",
                 {"value": None, "status": "not_found"})
    write_json(scenario_dir, data)

    record = load_scenario(scenario_dir)["expected_results"][FACTS]["project_duration_months"]["project_narrative"]
    assert record == {"value": None, "status": "not_found"}


def test_not_found_expected_fact_with_value_is_rejected(tmp_path):
    scenario_dir = make_scenario(tmp_path)
    data = read_json(scenario_dir)
    apply_change(data, f"{FACTS}.project_duration_months.project_narrative",
                 {"value": 24, "status": "not_found"})
    write_json(scenario_dir, data)

    with pytest.raises(ScenarioValidationError) as error:
        load_scenario(scenario_dir)
    assert_error_mentions(error, f"{FACTS}.project_duration_months.project_narrative.value",
                          "must be null when status is 'not_found'")
