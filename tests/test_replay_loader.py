"""Tests for grantcheck.replay_loader.

These tests check the offline replay loader: that a replay_facts.json file is
parsed strictly, follows the authoritative candidate-fact schema, belongs to
the right application, and quotes evidence that occurs in the text stored at
intake. Replay files are hand-written stand-ins for extractor output, so
passing these tests says nothing about live AI extraction accuracy.

The real sample_data/ files are only read. Every malformed replay is written
to pytest's tmp_path, starting from a copy of the real S01 replay.
"""

import codecs
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Temporary import-path adjustment (same approach as test_document_intake.py):
# src/grantcheck is not installed as a package yet, so make src/ importable.
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from grantcheck.document_intake import DOCUMENT_ROLES, intake_application  # noqa: E402
from grantcheck.fact_schema import FACT_SOURCES, SOURCE_RECORD_FIELDS  # noqa: E402
from grantcheck.replay_loader import (  # noqa: E402
    MAX_REPLAY_BYTES,
    ReplayValidationError,
    load_replay_facts,
)
from scenario_loader import discover_scenario_dirs, read_expected_results  # noqa: E402

SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"
SCENARIO_DIRS = discover_scenario_dirs(SAMPLE_DATA_DIR)
SCENARIO_IDS = [d.name for d in SCENARIO_DIRS]
BASELINE_DIR = SAMPLE_DATA_DIR / "S01_consistent"

REPLAY_FILE = "replay_facts.json"
FIXTURE_FOLDER = "S01_fixture"
WHERE = f"{FIXTURE_FOLDER}/{REPLAY_FILE}"  # location the loader reports for tmp replays

ROLE_FILENAMES = {
    "budget": "budget.txt",
    "budget_justification": "budget_justification.txt",
    "project_narrative": "project_narrative.txt",
}

CF = "candidate_facts"
DELETE = object()  # marker meaning "remove this field" in the change tables

# Paths to individual S01 source records, used by the change tables below.
DURATION_BUDGET = (CF, "project_duration_months", "budget")
ROLE_BUDGET = (CF, "personnel_role", "budget")
ROLE_JUSTIFICATION = (CF, "personnel_role", "budget_justification")
COST_BUDGET = (CF, "personnel_cost_usd", "budget")
COST_JUSTIFICATION = (CF, "personnel_cost_usd", "budget_justification")
EFFORT_BUDGET = (CF, "personnel_effort_percent", "budget")
TRAVEL_JUSTIFICATION = (CF, "travel_justification", "budget_justification")


# --- Helpers -----------------------------------------------------------------


def scenario_dir_for(scenario_id):
    for scenario_dir in SCENARIO_DIRS:
        if scenario_dir.name.startswith(f"{scenario_id}_"):
            return scenario_dir
    pytest.fail(f"Scenario {scenario_id} not found under {SAMPLE_DATA_DIR}")


def scenario_application(scenario_dir):
    """Run real document intake on a scenario's three source documents."""
    application_id = read_expected_results(scenario_dir)["application_id"]
    paths = [scenario_dir / ROLE_FILENAMES[role] for role in DOCUMENT_ROLES]
    application = intake_application(application_id, *paths)
    assert application["intake_status"] == "completed", f"test setup: {scenario_dir.name} intake failed"
    return application


def read_replay(scenario_dir):
    return json.loads((scenario_dir / REPLAY_FILE).read_text(encoding="utf-8"))


def baseline_text():
    """The real S01 replay re-serialized compactly, for text-level edits."""
    return json.dumps(read_replay(BASELINE_DIR))


def write_replay(tmp_path, content):
    """Write a replay to tmp_path/S01_fixture/replay_facts.json (bytes, text, or a JSON value)."""
    folder = tmp_path / FIXTURE_FOLDER
    folder.mkdir(exist_ok=True)
    path = folder / REPLAY_FILE
    if isinstance(content, str):
        content = content.encode("utf-8")
    elif not isinstance(content, bytes):
        content = json.dumps(content).encode("utf-8")
    path.write_bytes(content)
    return path


def replace_once(text, old, new):
    assert old in text, f"test setup: {old!r} not found in replay text"
    return text.replace(old, new, 1)


def apply_change(data, path, value):
    """Set (or delete, with DELETE) the field at path, e.g. ("candidate_facts", "personnel_role")."""
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value


def field(path, *extra):
    """The dotted field location the loader reports, e.g. candidate_facts.personnel_role.budget.value."""
    return ".".join(path + extra)


def key_order(obj):
    """Nested key order of a parsed JSON object, for order-preservation checks."""
    if isinstance(obj, dict):
        return [(key, key_order(value)) for key, value in obj.items()]
    return None


def assert_replay_error(application, replay_path, *fragments):
    with pytest.raises(ReplayValidationError) as error:
        load_replay_facts(application, replay_path)
    message = str(error.value)
    for fragment in fragments:
        assert fragment in message, f"expected {fragment!r} in error message: {message}"
    return message


def write_documents(folder, newline):
    """Write the S01 documents with exactly the given line ending ("\\n" or "\\r\\n")."""
    folder.mkdir()
    paths = []
    for role in DOCUMENT_ROLES:
        raw = (BASELINE_DIR / ROLE_FILENAMES[role]).read_bytes().replace(b"\r\n", b"\n")
        if newline == "\r\n":
            raw = raw.replace(b"\n", b"\r\n")
        path = folder / ROLE_FILENAMES[role]
        path.write_bytes(raw)
        paths.append(path)
    return paths


@pytest.fixture
def application():
    """A fresh, successful intake record for the real S01 documents."""
    return scenario_application(BASELINE_DIR)


# --- 1. The seven real scenario fixtures ---------------------------------------


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=SCENARIO_IDS)
def test_real_replay_loads_and_matches_fixture(scenario_dir):
    result = load_replay_facts(scenario_application(scenario_dir), scenario_dir / REPLAY_FILE)
    expected = read_replay(scenario_dir)[CF]

    assert result == expected
    assert key_order(result) == key_order(expected), "returned dict should keep the file's key order"


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=SCENARIO_IDS)
def test_real_replay_has_schema_shape(scenario_dir):
    result = load_replay_facts(scenario_application(scenario_dir), scenario_dir / REPLAY_FILE)

    assert set(result) == set(FACT_SOURCES)
    for fact, roles in FACT_SOURCES.items():
        assert set(result[fact]) == set(roles), f"{fact}: wrong source documents"
        for role in roles:
            assert set(result[fact][role]) == set(SOURCE_RECORD_FIELDS), f"{fact}.{role}: wrong fields"
    assert sum(len(sources) for sources in result.values()) == 12


def test_s05_missing_travel_records_load():
    scenario_dir = scenario_dir_for("S05")
    result = load_replay_facts(scenario_application(scenario_dir), scenario_dir / REPLAY_FILE)

    assert result["travel_cost_usd"]["budget"] == {
        "value": 8000, "status": "present", "evidence_text": "Travel Cost: $8,000",
    }
    for fact in ("travel_cost_usd", "travel_justification"):
        assert result[fact]["budget_justification"] == {
            "value": None, "status": "missing", "evidence_text": None,
        }, f"S05 {fact}.budget_justification should be null/missing with no evidence"


def test_s06_uncertain_record_keeps_verbatim_evidence():
    scenario_dir = scenario_dir_for("S06")
    application = scenario_application(scenario_dir)
    record = load_replay_facts(application, scenario_dir / REPLAY_FILE)["travel_justification"]["budget_justification"]

    assert record["status"] == "uncertain"
    assert record["value"] is None
    assert record["evidence_text"]
    assert record["evidence_text"] in application["documents"]["budget_justification"]["text"]


def test_s07_unprocessable_justification_records_load():
    # S07 is an extraction-stage exception, not an intake failure: intake
    # completed, and the replay marks every justification record unprocessable.
    scenario_dir = scenario_dir_for("S07")
    result = load_replay_facts(scenario_application(scenario_dir), scenario_dir / REPLAY_FILE)

    for fact, roles in FACT_SOURCES.items():
        for role in roles:
            record = result[fact][role]
            if role == "budget_justification":
                assert record == {"value": None, "status": "unprocessable", "evidence_text": None}, (
                    f"S07 {fact}.{role} should be null/unprocessable"
                )
            else:
                assert record["status"] == "present", f"S07 {fact}.{role} should be present"


# --- 6. Read-only behavior -----------------------------------------------------


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=SCENARIO_IDS)
def test_successful_load_leaves_application_unchanged(scenario_dir):
    application = scenario_application(scenario_dir)
    before = copy.deepcopy(application)

    load_replay_facts(application, scenario_dir / REPLAY_FILE)

    assert application == before
    assert CF not in application, "candidate facts must not be attached to the application"


def test_failed_load_leaves_application_unchanged(tmp_path, application):
    before = copy.deepcopy(application)
    data = read_replay(BASELINE_DIR)
    apply_change(data, ROLE_BUDGET + ("evidence_text",), "Not in the budget")

    with pytest.raises(ReplayValidationError):
        load_replay_facts(application, write_replay(tmp_path, data))

    assert application == before


def test_loading_does_not_modify_fixture_files():
    def fingerprints():
        return {d.name: hashlib.sha256((d / REPLAY_FILE).read_bytes()).hexdigest() for d in SCENARIO_DIRS}

    before = fingerprints()
    for scenario_dir in SCENARIO_DIRS:
        load_replay_facts(scenario_application(scenario_dir), scenario_dir / REPLAY_FILE)

    assert fingerprints() == before


# --- 2. Application validation -------------------------------------------------


def change_application(*path, value=DELETE):
    """Return a function that edits (or deletes) one field of an application copy."""
    def edit(application):
        apply_change(application, path, value)
        return application
    return edit


def replace_application(replacement):
    return lambda application: replacement


INVALID_APPLICATION_CASES = [
    ("not_a_dict_none", replace_application(None), "application must be a dict"),
    ("not_a_dict_list", replace_application([]), "application must be a dict"),
    ("missing_application_id", change_application("application_id"), "application_id must be a non-blank string"),
    ("application_id_none", change_application("application_id", value=None), "application_id must be a non-blank string"),
    ("application_id_empty", change_application("application_id", value=""), "application_id must be a non-blank string"),
    ("application_id_whitespace", change_application("application_id", value=" \t\n"), "application_id must be a non-blank string"),
    ("application_id_number", change_application("application_id", value=1), "application_id must be a non-blank string"),
    ("intake_exception", change_application("intake_status", value="exception"), "intake_status must be 'completed'"),
    ("intake_missing", change_application("intake_status"), "intake_status must be 'completed'"),
    ("intake_wrong_case", change_application("intake_status", value="Completed"), "intake_status must be 'completed'"),
    ("documents_missing", change_application("documents"), "documents must be a dict"),
    ("documents_list", change_application("documents", value=[]), "documents must be a dict"),
    *[
        (f"missing_role_{role}", change_application("documents", role), f"documents is missing {role!r}")
        for role in DOCUMENT_ROLES
    ],
    ("failed_document_none", change_application("documents", "budget_justification", value=None), "must be a dict"),
    ("document_record_string", change_application("documents", "project_narrative", value="text"), "must be a dict"),
    ("text_missing", change_application("documents", "budget", "text"), "['text'] must be a non-empty string"),
    ("text_empty", change_application("documents", "budget", "text", value=""), "['text'] must be a non-empty string"),
    ("text_none", change_application("documents", "budget", "text", value=None), "['text'] must be a non-empty string"),
    ("text_bytes", change_application("documents", "budget", "text", value=b"Budget"), "['text'] must be a non-empty string"),
]


@pytest.mark.parametrize(
    "edit, fragment",
    [(edit, fragment) for _, edit, fragment in INVALID_APPLICATION_CASES],
    ids=[case_id for case_id, _, _ in INVALID_APPLICATION_CASES],
)
def test_invalid_application_rejected_before_replay_access(tmp_path, monkeypatch, application, edit, fragment):
    bad_application = edit(copy.deepcopy(application))
    replay_path = write_replay(tmp_path, read_replay(BASELINE_DIR))  # a valid replay exists

    # Any attempt to open the replay file would fail the test with AssertionError.
    def fail_if_opened(*args, **kwargs):
        raise AssertionError("replay file was opened before application validation finished")

    monkeypatch.setattr(Path, "open", fail_if_opened)

    with pytest.raises(ValueError) as error:
        load_replay_facts(bad_application, replay_path)
    assert type(error.value) is ValueError, (
        f"application problems must raise plain ValueError, got {type(error.value).__name__}"
    )
    assert fragment in str(error.value)


def test_extra_application_fields_and_roles_are_allowed(application):
    application["processing_status"] = "anything"
    application["reviewer_notes"] = "not part of the loader contract"
    application["documents"]["appendix"] = {"filename": "appendix.txt", "text": "Extra document"}

    result = load_replay_facts(application, BASELINE_DIR / REPLAY_FILE)

    assert set(result) == set(FACT_SOURCES)


def test_document_metadata_other_than_text_is_not_required(application):
    for role in DOCUMENT_ROLES:
        application["documents"][role] = {"text": application["documents"][role]["text"]}

    assert set(load_replay_facts(application, BASELINE_DIR / REPLAY_FILE)) == set(FACT_SOURCES)


def test_whitespace_only_document_text_passes_application_check(application):
    # Accepted as an application; the replay then fails on evidence instead.
    application["documents"]["budget"]["text"] = "   "
    assert_replay_error(
        application,
        BASELINE_DIR / REPLAY_FILE,
        field(DURATION_BUDGET, "evidence_text"),
        "does not occur verbatim in the stored budget text",
    )


def test_application_id_is_compared_exactly(application):
    application["application_id"] = "GC-S01 "  # valid, but not identical to the replay's "GC-S01"
    assert_replay_error(application, BASELINE_DIR / REPLAY_FILE, "application_id", "does not match")


# --- 7. Exception categories ---------------------------------------------------


def test_replay_validation_error_is_a_value_error():
    assert issubclass(ReplayValidationError, ValueError)


@pytest.mark.parametrize("bad_path", [None, 123], ids=["none", "int"])
def test_unsupported_path_type_raises_type_error(application, bad_path):
    with pytest.raises(TypeError):
        load_replay_facts(application, bad_path)


def test_application_is_checked_before_path_type():
    with pytest.raises(ValueError) as error:
        load_replay_facts(None, None)
    assert type(error.value) is ValueError


def test_string_path_is_accepted(application):
    result = load_replay_facts(application, str(BASELINE_DIR / REPLAY_FILE))
    assert set(result) == set(FACT_SOURCES)


# --- 3. File reading and JSON parsing ------------------------------------------


def test_missing_replay_file(tmp_path, application):
    (tmp_path / FIXTURE_FOLDER).mkdir()
    assert_replay_error(application, tmp_path / FIXTURE_FOLDER / REPLAY_FILE, WHERE, "missing")


def test_directory_is_unreadable(tmp_path, application):
    folder_named_like_file = tmp_path / FIXTURE_FOLDER / REPLAY_FILE
    folder_named_like_file.mkdir(parents=True)
    assert_replay_error(application, folder_named_like_file, WHERE, "could not be read")


def test_file_at_size_limit_is_accepted(tmp_path, application):
    text = baseline_text()
    padded = text + " " * (MAX_REPLAY_BYTES - len(text.encode("utf-8")))  # trailing whitespace is valid JSON
    path = write_replay(tmp_path, padded)

    assert path.stat().st_size == MAX_REPLAY_BYTES
    assert set(load_replay_facts(application, path)) == set(FACT_SOURCES)


def test_file_over_size_limit_is_rejected(tmp_path, application):
    text = baseline_text()
    padded = text + " " * (MAX_REPLAY_BYTES + 1 - len(text.encode("utf-8")))  # otherwise valid
    path = write_replay(tmp_path, padded)

    assert path.stat().st_size == MAX_REPLAY_BYTES + 1
    assert_replay_error(application, path, WHERE, "exceeds")


@pytest.mark.parametrize(
    "content",
    [b'{"application_id": "\xff"}', b'"\xe2\x82"', b'"\xc0\xaf"'],
    ids=["invalid_byte", "truncated_sequence", "overlong_encoding"],
)
def test_invalid_utf8_is_rejected(tmp_path, application, content):
    assert_replay_error(application, write_replay(tmp_path, content), WHERE, "not valid UTF-8")


def test_leading_byte_order_mark_is_rejected(tmp_path, application):
    content = codecs.BOM_UTF8 + baseline_text().encode("utf-8")
    assert_replay_error(application, write_replay(tmp_path, content), WHERE, "byte order mark")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"  \r\n\t  ",
        b'{"application_id": "GC-S01",',
        b"{'application_id': 'GC-S01'}",
        b'{"application_id": "GC-S01",}',
        b'{"application_id": "GC-S01"} trailing text',
    ],
    ids=["empty", "whitespace_only", "truncated", "single_quotes", "trailing_comma", "extra_data"],
)
def test_malformed_json_is_rejected(tmp_path, application, content):
    assert_replay_error(application, write_replay(tmp_path, content), WHERE, "malformed JSON")


DUPLICATE_KEY_CASES = [
    ("top_level", lambda text: replace_once(text, "{", '{"application_id": "GC-S01", '), "application_id"),
    ("candidate_facts_level",
     lambda text: replace_once(text, '"candidate_facts": {', '"candidate_facts": {"personnel_role": {}, '),
     "personnel_role"),
    ("category_level",
     lambda text: replace_once(text, '"project_duration_months": {', '"project_duration_months": {"budget": {}, '),
     "budget"),
    ("record_level", lambda text: replace_once(text, '"value": 24, ', '"value": 24, "value": 24, '), "value"),
]


@pytest.mark.parametrize(
    "edit, key",
    [(edit, key) for _, edit, key in DUPLICATE_KEY_CASES],
    ids=[case_id for case_id, _, _ in DUPLICATE_KEY_CASES],
)
def test_duplicate_keys_are_rejected_at_every_level(tmp_path, application, edit, key):
    path = write_replay(tmp_path, edit(baseline_text()))
    assert_replay_error(application, path, WHERE, f"duplicate JSON key {key!r}")


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected(tmp_path, application, constant):
    text = replace_once(baseline_text(), '"value": 24, ', f'"value": {constant}, ')
    assert_replay_error(application, write_replay(tmp_path, text), WHERE, f"non-standard JSON constant {constant}")


@pytest.mark.parametrize("literal, shown", [("1e999", "inf"), ("-1e999", "-inf")], ids=["positive", "negative"])
def test_overflowing_float_is_rejected_as_non_finite(tmp_path, application, literal, shown):
    # The parser turns 1e999 into an infinite float without calling parse_constant.
    text = replace_once(baseline_text(), '"value": 24000, ', f'"value": {literal}, ')
    assert_replay_error(
        application, write_replay(tmp_path, text), WHERE,
        field(COST_BUDGET, "value"), f"must be a finite number, got {shown}",
    )


def test_overflowing_float_duration_fails_the_integer_check(tmp_path, application):
    text = replace_once(baseline_text(), '"value": 24, ', '"value": 1e999, ')
    assert_replay_error(
        application, write_replay(tmp_path, text), WHERE,
        field(DURATION_BUDGET, "value"), "must be an integer",
    )


def test_integer_beyond_configured_digit_limit_is_rejected(tmp_path, application):
    limit = sys.get_int_max_str_digits()
    if limit == 0:
        pytest.skip("this interpreter has no integer digit limit")
    if limit + 1 > MAX_REPLAY_BYTES // 2:
        pytest.skip("configured digit limit is too large to exceed within a replay file")

    too_long = "9" * (limit + 1)
    text = replace_once(baseline_text(), '"value": 24000, ', f'"value": {too_long}, ')
    assert_replay_error(application, write_replay(tmp_path, text), WHERE, "could not be parsed")


def test_large_integer_within_limit_is_accepted(tmp_path, application):
    # 400 digits is below Python's smallest allowed digit limit (640), so it always parses.
    # Integers are finite by definition, so no float conversion (and no overflow) happens.
    large = "9" * 400
    text = replace_once(baseline_text(), '"value": 24000, ', f'"value": {large}, ')

    result = load_replay_facts(application, write_replay(tmp_path, text))

    assert result["personnel_cost_usd"]["budget"]["value"] == int(large)


def test_excessive_nesting_is_rejected(tmp_path, application):
    depth = 100_000  # about 200 KB, well under the size limit
    path = write_replay(tmp_path, "[" * depth + "]" * depth)
    assert_replay_error(application, path, WHERE, "nested too deeply")


# --- 4 and 5. Schema, status, value, and evidence rules ------------------------


@pytest.mark.parametrize("content", ["[]", '"GC-S01"', "null", "42", "true"], ids=["array", "string", "null", "number", "boolean"])
def test_top_level_must_be_an_object(tmp_path, application, content):
    assert_replay_error(application, write_replay(tmp_path, content), WHERE, "(top level)", "must be a JSON object")


def test_unmodified_copy_is_valid(tmp_path, application):
    # Guards the change table below: each case fails only because of its own change.
    result = load_replay_facts(application, write_replay(tmp_path, read_replay(BASELINE_DIR)))
    assert result == read_replay(BASELINE_DIR)[CF]


# Each case: (test id, [(field path, new value or DELETE), ...], fragments the error must contain).
# The baseline is S01, where all 12 records are present.
INVALID_REPLAY_CASES = [
    # Top-level keys
    ("top_missing_application_id", [(("application_id",), DELETE)],
     ["(top level)", "missing keys ['application_id']"]),
    ("top_missing_candidate_facts", [((CF,), DELETE)],
     ["(top level)", "missing keys ['candidate_facts']"]),
    ("top_unexpected_key", [(("notes",), "x")],
     ["(top level)", "unexpected keys ['notes']"]),
    ("top_missing_and_unexpected", [((CF,), DELETE), (("facts",), {})],
     ["(top level)", "missing keys ['candidate_facts']", "unexpected keys ['facts']"]),
    # Replay application ID
    ("replay_id_number", [(("application_id",), 123)], ["application_id", "must be a non-blank string"]),
    ("replay_id_null", [(("application_id",), None)], ["application_id", "must be a non-blank string"]),
    ("replay_id_empty", [(("application_id",), "")], ["application_id", "must be a non-blank string"]),
    ("replay_id_whitespace", [(("application_id",), "   ")], ["application_id", "must be a non-blank string"]),
    ("replay_id_other_application", [(("application_id",), "GC-S02")], ["application_id", "does not match"]),
    ("replay_id_lowercase", [(("application_id",), "gc-s01")], ["application_id", "does not match"]),
    ("replay_id_trailing_space", [(("application_id",), "GC-S01 ")], ["application_id", "does not match"]),
    ("replay_id_leading_space", [(("application_id",), " GC-S01")], ["application_id", "does not match"]),
    # candidate_facts container and categories
    ("candidate_facts_array", [((CF,), [])], ["candidate_facts", "must be a JSON object, got array"]),
    ("candidate_facts_null", [((CF,), None)], ["candidate_facts", "must be a JSON object, got null"]),
    ("missing_category", [((CF, "travel_cost_usd"), DELETE)],
     ["candidate_facts", "missing keys ['travel_cost_usd']"]),
    ("unexpected_category", [((CF, "travel_purpose"), {})],
     ["candidate_facts", "unexpected keys ['travel_purpose']"]),
    # Category containers and source records
    ("category_string", [((CF, "personnel_role"), "Graduate Research Assistant")],
     ["candidate_facts.personnel_role", "must be a JSON object, got string"]),
    ("category_array", [((CF, "personnel_role"), [])],
     ["candidate_facts.personnel_role", "must be a JSON object, got array"]),
    ("missing_source_record", [((CF, "project_duration_months", "project_narrative"), DELETE)],
     ["candidate_facts.project_duration_months", "missing keys ['project_narrative']"]),
    ("extra_source_record",
     [((CF, "travel_justification", "budget"), {"value": None, "status": "missing", "evidence_text": None})],
     ["candidate_facts.travel_justification", "unexpected keys ['budget']"]),
    # Source-record structure
    ("record_string", [(ROLE_BUDGET, "x")], [field(ROLE_BUDGET), "must be a JSON object, got string"]),
    ("record_null", [(ROLE_BUDGET, None)], [field(ROLE_BUDGET), "must be a JSON object, got null"]),
    ("record_missing_field", [(ROLE_BUDGET + ("evidence_text",), DELETE)],
     [field(ROLE_BUDGET), "missing keys ['evidence_text']"]),
    ("record_unexpected_field", [(ROLE_BUDGET + ("confidence",), 0.9)],
     [field(ROLE_BUDGET), "unexpected keys ['confidence']"]),
    # Status
    ("status_unknown", [(COST_BUDGET + ("status",), "absent")], [field(COST_BUDGET, "status"), "'absent' is not one of"]),
    ("status_wrong_case", [(COST_BUDGET + ("status",), "Present")], [field(COST_BUDGET, "status"), "'Present' is not one of"]),
    ("status_trailing_space", [(COST_BUDGET + ("status",), "present ")],
     [field(COST_BUDGET, "status"), "'present ' is not one of"]),
    ("status_null", [(COST_BUDGET + ("status",), None)], [field(COST_BUDGET, "status"), "None is not one of"]),
    ("status_number", [(COST_BUDGET + ("status",), 1)], [field(COST_BUDGET, "status"), "1 is not one of"]),
    # Values of present records
    ("present_null_value", [(COST_BUDGET + ("value",), None)],
     [field(COST_BUDGET, "value"), "must not be null when status is 'present'"]),
    ("boolean_cost", [(COST_BUDGET + ("value",), True)], [field(COST_BUDGET, "value"), "must be a number, got boolean True"]),
    ("boolean_effort", [(EFFORT_BUDGET + ("value",), False)],
     [field(EFFORT_BUDGET, "value"), "must be a number, got boolean False"]),
    ("boolean_duration", [(DURATION_BUDGET + ("value",), True)],
     [field(DURATION_BUDGET, "value"), "must be an integer, got boolean True"]),
    ("float_duration", [(DURATION_BUDGET + ("value",), 24.0)],
     [field(DURATION_BUDGET, "value"), "must be an integer, got number 24.0"]),
    ("string_duration", [(DURATION_BUDGET + ("value",), "24")],
     [field(DURATION_BUDGET, "value"), "must be an integer, got string '24'"]),
    ("string_cost", [(COST_BUDGET + ("value",), "24000")],
     [field(COST_BUDGET, "value"), "must be a number, got string '24000'"]),
    ("role_number", [(ROLE_BUDGET + ("value",), 5)], [field(ROLE_BUDGET, "value"), "must be a string, got number 5"]),
    ("role_empty", [(ROLE_BUDGET + ("value",), "")], [field(ROLE_BUDGET, "value"), "must be a non-blank string"]),
    ("role_whitespace", [(ROLE_BUDGET + ("value",), "   ")], [field(ROLE_BUDGET, "value"), "must be a non-blank string"]),
    ("travel_justification_unapproved", [(TRAVEL_JUSTIFICATION + ("value",), "good")],
     [field(TRAVEL_JUSTIFICATION, "value"), "'good' is not one of ['adequate']"]),
    ("travel_justification_wrong_case", [(TRAVEL_JUSTIFICATION + ("value",), "Adequate")],
     [field(TRAVEL_JUSTIFICATION, "value"), "'Adequate' is not one of ['adequate']"]),
    ("travel_justification_number", [(TRAVEL_JUSTIFICATION + ("value",), 1)],
     [field(TRAVEL_JUSTIFICATION, "value"), "must be a string, got number 1"]),
    # Values of non-present records must be null
    ("missing_with_value", [(COST_JUSTIFICATION + ("status",), "missing")],
     [field(COST_JUSTIFICATION, "value"), "must be null when status is 'missing', got 24000"]),
    ("uncertain_with_value", [(TRAVEL_JUSTIFICATION + ("status",), "uncertain")],
     [field(TRAVEL_JUSTIFICATION, "value"), "must be null when status is 'uncertain'"]),
    ("unprocessable_with_value", [(ROLE_JUSTIFICATION + ("status",), "unprocessable")],
     [field(ROLE_JUSTIFICATION, "value"), "must be null when status is 'unprocessable'"]),
    # Evidence of present records
    ("present_null_evidence", [(DURATION_BUDGET + ("evidence_text",), None)],
     [field(DURATION_BUDGET, "evidence_text"), "must be a non-blank string when status is 'present', got null"]),
    ("present_empty_evidence", [(DURATION_BUDGET + ("evidence_text",), "")],
     [field(DURATION_BUDGET, "evidence_text"), "must be null or a non-blank string"]),
    ("present_whitespace_evidence", [(DURATION_BUDGET + ("evidence_text",), " \t ")],
     [field(DURATION_BUDGET, "evidence_text"), "must be null or a non-blank string"]),
    ("evidence_number", [(DURATION_BUDGET + ("evidence_text",), 24)],
     [field(DURATION_BUDGET, "evidence_text"), "must be null or a non-blank string, got number"]),
    ("evidence_array", [(DURATION_BUDGET + ("evidence_text",), ["Project Duration: 24 months"])],
     [field(DURATION_BUDGET, "evidence_text"), "must be null or a non-blank string, got array"]),
    # Provenance: exact, case-sensitive substring of the stored text for that document
    ("evidence_wrong_case", [(DURATION_BUDGET + ("evidence_text",), "project duration: 24 months")],
     [field(DURATION_BUDGET, "evidence_text"), "does not occur verbatim in the stored budget text"]),
    ("evidence_extra_space", [(DURATION_BUDGET + ("evidence_text",), "Project Duration:  24 months")],
     [field(DURATION_BUDGET, "evidence_text"), "does not occur verbatim in the stored budget text"]),
    ("evidence_leading_space", [(DURATION_BUDGET + ("evidence_text",), " Project Duration: 24 months")],
     [field(DURATION_BUDGET, "evidence_text"), "does not occur verbatim in the stored budget text"]),
    ("evidence_fabricated", [(DURATION_BUDGET + ("evidence_text",), "The project lasts 24 months.")],
     [field(DURATION_BUDGET, "evidence_text"), "does not occur verbatim in the stored budget text"]),
    ("evidence_from_other_document", [(COST_JUSTIFICATION + ("evidence_text",), "Personnel Cost: $24,000")],
     [field(COST_JUSTIFICATION, "evidence_text"), "does not occur verbatim in the stored budget_justification text"]),
    # Evidence of non-present records: optional, but still non-blank and verbatim if given
    ("nonpresent_whitespace_evidence",
     [(TRAVEL_JUSTIFICATION + ("status",), "uncertain"), (TRAVEL_JUSTIFICATION + ("value",), None),
      (TRAVEL_JUSTIFICATION + ("evidence_text",), "   ")],
     [field(TRAVEL_JUSTIFICATION, "evidence_text"), "must be null or a non-blank string"]),
    ("nonpresent_unverifiable_evidence",
     [(TRAVEL_JUSTIFICATION + ("status",), "missing"), (TRAVEL_JUSTIFICATION + ("value",), None),
      (TRAVEL_JUSTIFICATION + ("evidence_text",), "Travel is fully justified.")],
     [field(TRAVEL_JUSTIFICATION, "evidence_text"), "does not occur verbatim in the stored budget_justification text"]),
]


@pytest.mark.parametrize(
    "changes, fragments",
    [(changes, fragments) for _, changes, fragments in INVALID_REPLAY_CASES],
    ids=[case_id for case_id, _, _ in INVALID_REPLAY_CASES],
)
def test_invalid_replay_content_is_rejected(tmp_path, application, changes, fragments):
    data = read_replay(BASELINE_DIR)
    for path, value in changes:
        apply_change(data, path, value)

    # Every message names the scenario folder and file, plus the field and reason.
    assert_replay_error(application, write_replay(tmp_path, data), WHERE, *fragments)


# Variations that must still load. The loader checks structure and provenance,
# not whether a value is the right interpretation of its evidence.
VALID_VARIATION_CASES = [
    ("fractional_cost", [(COST_BUDGET + ("value",), 24000.5)]),
    ("fractional_effort", [(EFFORT_BUDGET + ("value",), 37.5)]),
    ("value_not_checked_against_evidence", [(COST_BUDGET + ("value",), 99999)]),
    ("missing_without_evidence",
     [(TRAVEL_JUSTIFICATION + ("status",), "missing"), (TRAVEL_JUSTIFICATION + ("value",), None),
      (TRAVEL_JUSTIFICATION + ("evidence_text",), None)]),
    ("uncertain_with_verbatim_evidence",
     [(TRAVEL_JUSTIFICATION + ("status",), "uncertain"), (TRAVEL_JUSTIFICATION + ("value",), None)]),
    ("unprocessable_without_evidence",
     [(ROLE_JUSTIFICATION + ("status",), "unprocessable"), (ROLE_JUSTIFICATION + ("value",), None),
      (ROLE_JUSTIFICATION + ("evidence_text",), None)]),
]


@pytest.mark.parametrize(
    "changes",
    [changes for _, changes in VALID_VARIATION_CASES],
    ids=[case_id for case_id, _ in VALID_VARIATION_CASES],
)
def test_valid_variation_loads(tmp_path, application, changes):
    data = read_replay(BASELINE_DIR)
    for path, value in changes:
        apply_change(data, path, value)

    result = load_replay_facts(application, write_replay(tmp_path, data))

    assert result == data[CF]


def test_repeated_excerpt_need_not_be_unique(tmp_path, application):
    excerpt = "Research Assistant"
    assert application["documents"]["budget_justification"]["text"].count(excerpt) >= 2, (
        "test setup: excerpt should occur more than once"
    )
    data = read_replay(BASELINE_DIR)
    apply_change(data, ROLE_JUSTIFICATION + ("evidence_text",), excerpt)

    result = load_replay_facts(application, write_replay(tmp_path, data))

    assert result["personnel_role"]["budget_justification"]["evidence_text"] == excerpt


def test_key_order_is_not_required_and_is_preserved(tmp_path, application):
    def reverse_keys(obj):
        if isinstance(obj, dict):
            return {key: reverse_keys(obj[key]) for key in reversed(list(obj))}
        return obj

    data = reverse_keys(read_replay(BASELINE_DIR))
    result = load_replay_facts(application, write_replay(tmp_path, data))

    assert list(result) == list(reversed(list(FACT_SOURCES)))
    assert key_order(result) == key_order(data[CF])


# --- Deterministic, fail-fast reporting ----------------------------------------


def test_first_record_in_schema_order_is_reported(tmp_path, application):
    data = read_replay(BASELINE_DIR)
    apply_change(data, TRAVEL_JUSTIFICATION + ("value",), "good")   # last category in the schema
    apply_change(data, DURATION_BUDGET + ("status",), "absent")      # first category in the schema
    data[CF] = {key: data[CF][key] for key in reversed(list(data[CF]))}  # travel_justification first in the file

    message = assert_replay_error(application, write_replay(tmp_path, data), field(DURATION_BUDGET, "status"))
    assert "travel_justification" not in message


def test_category_containers_are_checked_before_records(tmp_path, application):
    data = read_replay(BASELINE_DIR)
    apply_change(data, DURATION_BUDGET + ("status",), "absent")  # record-level problem, first category
    apply_change(data, (CF, "travel_justification", "budget"),   # container-level problem, last category
                 {"value": None, "status": "missing", "evidence_text": None})

    assert_replay_error(
        application, write_replay(tmp_path, data),
        "candidate_facts.travel_justification", "unexpected keys ['budget']",
    )


# --- 5. Multiline evidence and exact line endings ------------------------------

MULTILINE_PARTS = (
    "The total requested personnel cost for the Graduate",
    "Research Assistant is $24,000.",
)


@pytest.mark.parametrize(
    "document_newline, evidence_newline, should_load",
    [("\n", "\n", True), ("\r\n", "\r\n", True), ("\n", "\r\n", False), ("\r\n", "\n", False)],
    ids=["lf_document_lf_evidence", "crlf_document_crlf_evidence",
         "lf_document_crlf_evidence", "crlf_document_lf_evidence"],
)
def test_multiline_evidence_matches_line_endings_exactly(tmp_path, document_newline, evidence_newline, should_load):
    # Documents are written as exact bytes, so the result never depends on git's checkout settings.
    application = intake_application("GC-S01", *write_documents(tmp_path / "documents", document_newline))
    stored_text = application["documents"]["budget_justification"]["text"]
    assert ("\r\n" in stored_text) == (document_newline == "\r\n"), "test setup: wrong line endings"

    excerpt = evidence_newline.join(MULTILINE_PARTS)
    data = read_replay(BASELINE_DIR)
    apply_change(data, COST_JUSTIFICATION + ("evidence_text",), excerpt)
    replay_path = write_replay(tmp_path, data)

    if should_load:
        result = load_replay_facts(application, replay_path)
        assert result["personnel_cost_usd"]["budget_justification"]["evidence_text"] == excerpt
    else:
        assert_replay_error(
            application, replay_path,
            field(COST_JUSTIFICATION, "evidence_text"), "does not occur verbatim",
        )


@pytest.mark.parametrize("document_newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_single_line_fixture_evidence_works_with_either_line_ending(tmp_path, document_newline):
    application = intake_application("GC-S01", *write_documents(tmp_path / "documents", document_newline))
    result = load_replay_facts(application, BASELINE_DIR / REPLAY_FILE)
    assert result == read_replay(BASELINE_DIR)[CF]


# --- not_found (Increment 1A) ---------------------------------------------------


def test_well_formed_not_found_record_loads(tmp_path, application):
    # FACT_STATUSES now includes not_found, so replay files may contain it.
    data = read_replay(BASELINE_DIR)
    apply_change(data, TRAVEL_JUSTIFICATION, {"value": None, "status": "not_found", "evidence_text": None})

    result = load_replay_facts(application, write_replay(tmp_path, data))

    assert result["travel_justification"]["budget_justification"]["status"] == "not_found"


@pytest.mark.parametrize("record_field, bad", [
    ("value", "adequate"),
    ("evidence_text", "These funds will support travel to coastal monitoring"),  # verbatim, but not allowed
], ids=["with_value", "with_evidence"])
def test_malformed_not_found_record_is_rejected(tmp_path, application, record_field, bad):
    record = {"value": None, "status": "not_found", "evidence_text": None, record_field: bad}
    data = read_replay(BASELINE_DIR)
    apply_change(data, TRAVEL_JUSTIFICATION, record)

    assert_replay_error(application, write_replay(tmp_path, data), WHERE, field(TRAVEL_JUSTIFICATION, record_field))
