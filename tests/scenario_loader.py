"""Load and validate the GrantCheck sample scenarios.

Each folder under sample_data/ named like "S01_consistent" holds one synthetic
grant application (three .txt source documents) plus an expected_results.json
describing what GrantCheck should produce for it.

This module only reads files and checks that they follow the approved test
schema. It does not extract facts, evaluate cross-document rules, or decide
whether human review is needed. In particular, S07's "[UNREADABLE DOCUMENT
CONTENT]" placeholder is ordinary readable text here; it is loaded like any
other document.

Public entry points:
    load_scenario(scenario_path)        -> dict for one scenario folder
    load_all_scenarios(sample_data_path) -> list of those dicts, sorted by folder
    validate_expected_results(data, ...) -> the shared schema validator

Every problem raises ScenarioValidationError with a message of the form
"<scenario folder>/<file>: <field>: <what is wrong>".
"""

import json
import re
from pathlib import Path

# Scenario folders look like "S01_consistent": "S", two digits, underscore, name.
SCENARIO_DIR_PATTERN = re.compile(r"^(S\d{2})_.+$")
APPLICATION_ID_PATTERN = re.compile(r"^GC-S\d{2}$")

SOURCE_DOCUMENT_FILES = {
    "budget": "budget.txt",
    "budget_justification": "budget_justification.txt",
    "project_narrative": "project_narrative.txt",
}
EXPECTED_RESULTS_FILE = "expected_results.json"

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

ALLOWED_FACT_STATUSES = {"present", "missing", "uncertain", "unprocessable", "not_found"}
EXPECTED_RULE_IDS = {"R-01", "R-02", "R-03", "R-04"}
ALLOWED_RULE_OUTCOMES = {"pass", "finding", "uncertain", "not_evaluated"}
ALLOWED_PROCESSING_STATUSES = {"completed", "exception"}


class ScenarioValidationError(ValueError):
    """A scenario folder or its expected results break the approved schema."""


def _fail(where, field, message):
    raise ScenarioValidationError(f"{where}: {field}: {message}")


def _require(data, key, where, field_prefix=""):
    """Return data[key], failing descriptively if data is not an object or lacks key."""
    field = f"{field_prefix}{key}"
    if not isinstance(data, dict):
        _fail(where, field_prefix.rstrip(".") or "<top level>", "must be a JSON object")
    if key not in data:
        _fail(where, field, "is missing")
    return data[key]


def _is_number(value):
    """True for int/float but not bool (in Python, True and False are ints)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_exact_keys(actual, expected, where, field):
    actual_keys = set(actual)
    if actual_keys != expected:
        _fail(
            where,
            field,
            f"unexpected keys. Missing: {sorted(expected - actual_keys)}, "
            f"extra: {sorted(actual_keys - expected)}",
        )


# --- Discovery and file reading ---------------------------------------------


def discover_scenario_dirs(sample_data_path):
    """Return S##_... scenario folders sorted by name; other entries are ignored."""
    sample_data_path = Path(sample_data_path)
    if not sample_data_path.is_dir():
        raise ScenarioValidationError(f"Sample data folder not found: {sample_data_path}")
    return sorted(
        (path for path in sample_data_path.iterdir()
         if path.is_dir() and SCENARIO_DIR_PATTERN.match(path.name)),
        key=lambda path: path.name,
    )


def folder_scenario_id(scenario_path):
    """Return the "S01"-style ID from a scenario folder name, or fail."""
    scenario_path = Path(scenario_path)
    match = SCENARIO_DIR_PATTERN.match(scenario_path.name)
    if not match:
        raise ScenarioValidationError(
            f"{scenario_path.name}: not a scenario folder (expected a name like S01_description)"
        )
    return match.group(1)


def read_source_documents(scenario_path):
    """Return {"budget": text, ...} for the three required source documents."""
    scenario_path = Path(scenario_path)
    documents = {}
    for document_name, file_name in SOURCE_DOCUMENT_FILES.items():
        file_path = scenario_path / file_name
        if not file_path.is_file():
            raise ScenarioValidationError(f"{scenario_path.name}/{file_name}: required file is missing")
        documents[document_name] = file_path.read_text(encoding="utf-8")
    return documents


def read_expected_results(scenario_path):
    """Parse expected_results.json without validating its schema."""
    scenario_path = Path(scenario_path)
    where = f"{scenario_path.name}/{EXPECTED_RESULTS_FILE}"
    results_path = scenario_path / EXPECTED_RESULTS_FILE
    if not results_path.is_file():
        raise ScenarioValidationError(f"{where}: required file is missing")
    try:
        return json.loads(results_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ScenarioValidationError(f"{where}: not valid JSON ({error})") from error


# --- Schema validation, one section at a time -------------------------------
# validate_expected_results() runs these in order. Each section can also be
# called on its own (the schema tests do this to report failures per section);
# missing fields still fail descriptively, though later sections assume the
# earlier ones' type checks have passed.


def validate_top_level(data, where):
    if not isinstance(data, dict):
        _fail(where, "<top level>", "must be a JSON object")
    _check_exact_keys(data, EXPECTED_TOP_LEVEL_KEYS, where, "<top level>")


def validate_identity(data, where, folder_id=None):
    scenario_id = _require(data, "scenario_id", where)
    application_id = _require(data, "application_id", where)
    scenario_name = _require(data, "scenario_name", where)

    if not isinstance(scenario_id, str):
        _fail(where, "scenario_id", f"must be a string, got {scenario_id!r}")
    if folder_id is not None and scenario_id != folder_id:
        _fail(where, "scenario_id", f"is {scenario_id!r} but the folder name says {folder_id!r}")
    if not isinstance(application_id, str) or not APPLICATION_ID_PATTERN.match(application_id):
        _fail(where, "application_id", f"{application_id!r} does not match GC-Sxx")
    if application_id != f"GC-{scenario_id}":
        _fail(where, "application_id", f"is {application_id!r}, expected 'GC-{scenario_id}'")
    if not isinstance(scenario_name, str) or not scenario_name.strip():
        _fail(where, "scenario_name", "must be a non-empty string")


def validate_fact_categories(data, where):
    """Check the six fact categories and each one's source-document keys."""
    facts = _require(data, "expected_normalized_values", where)
    if not isinstance(facts, dict):
        _fail(where, "expected_normalized_values", "must be a JSON object")
    _check_exact_keys(facts, set(EXPECTED_FACT_DOCUMENTS), where, "expected_normalized_values")

    for fact_name, expected_documents in EXPECTED_FACT_DOCUMENTS.items():
        fact_field = f"expected_normalized_values.{fact_name}"
        documents = facts[fact_name]
        if not isinstance(documents, dict):
            _fail(where, fact_field, "must be a JSON object keyed by source document")
        _check_exact_keys(documents, expected_documents, where, fact_field)


def validate_fact_entries(data, where):
    """Check every source-specific {"value", "status"} entry."""
    facts = _require(data, "expected_normalized_values", where)
    for fact_name, documents in facts.items():
        for document_name, entry in documents.items():
            _validate_fact_entry(
                fact_name, entry, where, f"expected_normalized_values.{fact_name}.{document_name}"
            )


def _validate_fact_entry(fact_name, entry, where, field):
    if not isinstance(entry, dict):
        _fail(where, field, f"must be an object with 'value' and 'status', got {entry!r}")
    _check_exact_keys(entry, {"value", "status"}, where, field)

    status, value = entry["status"], entry["value"]
    if status not in ALLOWED_FACT_STATUSES:
        _fail(where, f"{field}.status", f"{status!r} is not one of {sorted(ALLOWED_FACT_STATUSES)}")

    if status != "present":
        if value is not None:
            _fail(where, f"{field}.value", f"must be null when status is {status!r}, got {value!r}")
        return

    if value is None:
        _fail(where, f"{field}.value", "must not be null when status is 'present'")
    if fact_name in NUMERIC_FACTS and not _is_number(value):
        _fail(where, f"{field}.value", f"must be a number, got {value!r}")
    if fact_name in TEXT_FACTS and not (isinstance(value, str) and value.strip()):
        _fail(where, f"{field}.value", f"must be a non-empty string, got {value!r}")
    if fact_name == "travel_justification" and value not in TRAVEL_JUSTIFICATION_VALUES:
        _fail(where, f"{field}.value", f"{value!r} is not one of {sorted(TRAVEL_JUSTIFICATION_VALUES)}")


def validate_rule_outcomes(data, where):
    outcomes = _require(data, "expected_rule_outcomes", where)
    if not isinstance(outcomes, dict):
        _fail(where, "expected_rule_outcomes", "must be a JSON object")
    _check_exact_keys(outcomes, EXPECTED_RULE_IDS, where, "expected_rule_outcomes")
    for rule_id, outcome in outcomes.items():
        if outcome not in ALLOWED_RULE_OUTCOMES:
            _fail(
                where,
                f"expected_rule_outcomes.{rule_id}",
                f"{outcome!r} is not one of {sorted(ALLOWED_RULE_OUTCOMES)}",
            )


def validate_status_fields(data, where):
    status = _require(data, "expected_processing_status", where)
    review_required = _require(data, "expected_review_required", where)
    finding_count = _require(data, "expected_finding_count", where)

    if status not in ALLOWED_PROCESSING_STATUSES:
        _fail(where, "expected_processing_status",
              f"{status!r} is not one of {sorted(ALLOWED_PROCESSING_STATUSES)}")
    if not isinstance(review_required, bool):
        _fail(where, "expected_review_required", f"must be true or false, got {review_required!r}")
    if not _is_nonnegative_int(finding_count):
        _fail(where, "expected_finding_count", f"must be a nonnegative integer, got {finding_count!r}")


def validate_consistency(data, where):
    outcomes = list(_require(data, "expected_rule_outcomes", where).values())
    finding_count = _require(data, "expected_finding_count", where)
    review_required = _require(data, "expected_review_required", where)
    status = _require(data, "expected_processing_status", where)

    # Current test convention: one finding per rule whose outcome is "finding".
    finding_outcomes = outcomes.count("finding")
    if finding_count != finding_outcomes:
        _fail(where, "expected_finding_count",
              f"is {finding_count}, but {finding_outcomes} rule(s) have outcome 'finding'")
    if ("finding" in outcomes or "uncertain" in outcomes) and review_required is not True:
        _fail(where, "expected_review_required",
              "must be true when any rule outcome is 'finding' or 'uncertain'")
    if status == "exception" and review_required is not True:
        _fail(where, "expected_review_required",
              "must be true when expected_processing_status is 'exception'")


def validate_expected_results(data, where=EXPECTED_RESULTS_FILE, folder_id=None):
    """The shared validator: raise ScenarioValidationError on the first problem found."""
    validate_top_level(data, where)
    validate_identity(data, where, folder_id)
    validate_fact_categories(data, where)
    validate_fact_entries(data, where)
    validate_rule_outcomes(data, where)
    validate_status_fields(data, where)
    validate_consistency(data, where)


# --- Public loaders ----------------------------------------------------------


def load_scenario(scenario_path):
    """Load and validate one scenario folder.

    Returns a plain dict:
        {
            "scenario_id": "S01",
            "folder_name": "S01_consistent",
            "path": Path to the folder,
            "documents": {"budget": text, "budget_justification": text,
                          "project_narrative": text},
            "expected_results": parsed expected_results.json,
        }
    """
    scenario_path = Path(scenario_path)
    folder_id = folder_scenario_id(scenario_path)
    if not scenario_path.is_dir():
        raise ScenarioValidationError(f"{scenario_path.name}: scenario folder not found at {scenario_path}")

    documents = read_source_documents(scenario_path)
    expected_results = read_expected_results(scenario_path)
    validate_expected_results(
        expected_results,
        where=f"{scenario_path.name}/{EXPECTED_RESULTS_FILE}",
        folder_id=folder_id,
    )
    return {
        "scenario_id": folder_id,
        "folder_name": scenario_path.name,
        "path": scenario_path,
        "documents": documents,
        "expected_results": expected_results,
    }


def load_all_scenarios(sample_data_path):
    """Load and validate every S##_... folder under sample_data_path, sorted by name."""
    scenario_dirs = discover_scenario_dirs(sample_data_path)
    if not scenario_dirs:
        raise ScenarioValidationError(
            f"No scenario folders (named like S01_description) found under {Path(sample_data_path)}"
        )
    return [load_scenario(scenario_dir) for scenario_dir in scenario_dirs]
