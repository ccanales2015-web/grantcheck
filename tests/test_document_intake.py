"""Tests for grantcheck.document_intake.

The real sample_data/ scenarios are only read. Failure cases are built in
pytest's tmp_path so the fixtures are never touched.
"""

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Temporary import-path adjustment: src/grantcheck is not installed as a
# package yet, so make src/ importable. Remove this once the project has
# packaging (e.g. pyproject.toml + `pip install -e .`) or a pytest pythonpath setting.
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from grantcheck.document_intake import (  # noqa: E402  (import after path setup)
    DOCUMENT_ROLES,
    MAX_DOCUMENT_BYTES,
    intake_application,
)
from scenario_loader import discover_scenario_dirs, read_expected_results  # noqa: E402

SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"
SCENARIO_DIRS = discover_scenario_dirs(SAMPLE_DATA_DIR)

ROLE_FILENAMES = {
    "budget": "budget.txt",
    "budget_justification": "budget_justification.txt",
    "project_narrative": "project_narrative.txt",
}


# --- Helpers -----------------------------------------------------------------


def scenario_paths(scenario_dir):
    """The three document paths of a scenario folder, in intake argument order."""
    return [scenario_dir / ROLE_FILENAMES[role] for role in DOCUMENT_ROLES]


def write_valid_documents(folder):
    """Write three small valid documents and return their paths in argument order."""
    paths = []
    for role in DOCUMENT_ROLES:
        path = folder / ROLE_FILENAMES[role]
        path.write_bytes(f"Sample {role} text.\n".encode("utf-8"))
        paths.append(path)
    return paths


def intake(paths, application_id="GC-TEST"):
    return intake_application(application_id, *paths)


def assert_failed_intake(result, expected_errors):
    """Check the exception statuses and that exactly the expected documents failed."""
    assert result["intake_status"] == "exception"
    assert result["processing_status"] == "exception"
    assert result["intake_errors"] == expected_errors
    for error in expected_errors:
        assert result["documents"][error["document"]] is None, (
            f"{error['document']} failed intake, so its documents entry should be None"
        )


# --- Successful intake of the real scenarios ---------------------------------


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=[d.name for d in SCENARIO_DIRS])
def test_real_scenario_intake_succeeds(scenario_dir):
    application_id = read_expected_results(scenario_dir)["application_id"]

    result = intake(scenario_paths(scenario_dir), application_id)

    assert result["application_id"] == application_id
    assert result["intake_status"] == "completed"
    assert result["processing_status"] == "pending"
    assert result["intake_errors"] == []
    assert set(result["documents"]) == set(DOCUMENT_ROLES)


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=[d.name for d in SCENARIO_DIRS])
def test_real_scenario_documents_match_source_bytes(scenario_dir):
    application_id = read_expected_results(scenario_dir)["application_id"]
    result = intake(scenario_paths(scenario_dir), application_id)

    for role in DOCUMENT_ROLES:
        source = scenario_dir / ROLE_FILENAMES[role]
        original_bytes = source.read_bytes()
        document = result["documents"][role]

        assert set(document) == {"filename", "text", "sha256"}
        assert document["filename"] == ROLE_FILENAMES[role]
        assert document["text"] == original_bytes.decode("utf-8"), f"{role}: text is not exact"
        assert document["sha256"] == hashlib.sha256(original_bytes).hexdigest()


def test_text_keeps_original_line_endings():
    # The fixtures use Windows line endings; intake must not translate them.
    scenario_dir = SAMPLE_DATA_DIR / "S01_consistent"
    result = intake(scenario_paths(scenario_dir), "GC-S01")
    original = (scenario_dir / "budget.txt").read_bytes()
    assert ("\r\n" in result["documents"]["budget"]["text"]) == (b"\r\n" in original)


def test_s07_placeholder_passes_intake():
    # Intake only checks the file is readable UTF-8; recognising the placeholder
    # as unprocessable content is a later stage's job.
    scenario_dir = SAMPLE_DATA_DIR / "S07_unprocessable_document"
    result = intake(scenario_paths(scenario_dir), "GC-S07")

    assert result["intake_status"] == "completed"
    assert result["processing_status"] == "pending"
    assert result["intake_errors"] == []
    assert result["documents"]["budget_justification"]["text"].startswith("[UNREADABLE DOCUMENT CONTENT]")


def test_result_is_plain_dict(tmp_path):
    result = intake(write_valid_documents(tmp_path))
    assert type(result) is dict
    assert set(result) == {
        "application_id", "documents", "intake_errors", "intake_status", "processing_status",
    }
    assert type(result["documents"]) is dict
    assert type(result["intake_errors"]) is list


def test_accepts_string_paths(tmp_path):
    result = intake([str(path) for path in write_valid_documents(tmp_path)])
    assert result["intake_status"] == "completed"


# --- Single-document failures ------------------------------------------------


@pytest.mark.parametrize("failed_role", DOCUMENT_ROLES)
def test_missing_document_keeps_the_others(tmp_path, failed_role):
    paths = write_valid_documents(tmp_path)
    index = DOCUMENT_ROLES.index(failed_role)
    paths[index].unlink()

    result = intake(paths)

    assert_failed_intake(result, [{"document": failed_role, "reason": "missing"}])
    for role in DOCUMENT_ROLES:
        if role != failed_role:
            assert result["documents"][role]["text"] == f"Sample {role} text.\n", (
                f"{role} was read successfully and should be kept"
            )


def test_missing_parent_folder_is_missing(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[0] = tmp_path / "no_such_folder" / "budget.txt"
    assert_failed_intake(intake(paths), [{"document": "budget", "reason": "missing"}])


def test_invalid_utf8_is_unreadable(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[1].write_bytes(b"Budget \xff\xfe justification")
    assert_failed_intake(intake(paths), [{"document": "budget_justification", "reason": "unreadable"}])


def test_directory_path_is_unreadable(tmp_path):
    # A path that exists but cannot be read as a file.
    paths = write_valid_documents(tmp_path)
    folder = tmp_path / "a_folder.txt"
    folder.mkdir()
    paths[2] = folder
    assert_failed_intake(intake(paths), [{"document": "project_narrative", "reason": "unreadable"}])


def test_zero_byte_file_is_empty(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[0].write_bytes(b"")
    assert_failed_intake(intake(paths), [{"document": "budget", "reason": "empty"}])


def test_whitespace_only_file_passes_intake(tmp_path):
    # Not semantically useful, but readable: intake does not judge content.
    paths = write_valid_documents(tmp_path)
    paths[1].write_bytes(b"  \r\n\t\n")

    result = intake(paths)

    assert result["intake_status"] == "completed"
    assert result["documents"]["budget_justification"]["text"] == "  \r\n\t\n"


# --- Size limit --------------------------------------------------------------


def test_file_at_exact_size_limit_is_accepted(tmp_path):
    paths = write_valid_documents(tmp_path)
    content = b"a" * MAX_DOCUMENT_BYTES
    paths[2].write_bytes(content)

    result = intake(paths)

    assert MAX_DOCUMENT_BYTES == 1_048_576
    assert result["intake_status"] == "completed"
    narrative = result["documents"]["project_narrative"]
    assert len(narrative["text"]) == MAX_DOCUMENT_BYTES
    assert narrative["sha256"] == hashlib.sha256(content).hexdigest()


def test_file_one_byte_over_limit_is_too_large(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[2].write_bytes(b"a" * (MAX_DOCUMENT_BYTES + 1))
    assert_failed_intake(intake(paths), [{"document": "project_narrative", "reason": "file_too_large"}])


def test_much_larger_file_is_too_large(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[0].write_bytes(b"a" * (MAX_DOCUMENT_BYTES * 3))
    assert_failed_intake(intake(paths), [{"document": "budget", "reason": "file_too_large"}])


def test_oversized_invalid_utf8_reports_size_first(tmp_path):
    # The size limit is checked before decoding.
    paths = write_valid_documents(tmp_path)
    paths[0].write_bytes(b"\xff" * (MAX_DOCUMENT_BYTES + 1))
    assert_failed_intake(intake(paths), [{"document": "budget", "reason": "file_too_large"}])


# --- Multiple failures -------------------------------------------------------


def test_all_failures_are_collected_in_role_order(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[0].unlink()                                          # budget: missing
    paths[1].write_bytes(b"")                                  # justification: empty
    paths[2].write_bytes(b"a" * (MAX_DOCUMENT_BYTES + 1))      # narrative: too large

    result = intake(paths)

    assert_failed_intake(result, [
        {"document": "budget", "reason": "missing"},
        {"document": "budget_justification", "reason": "empty"},
        {"document": "project_narrative", "reason": "file_too_large"},
    ])
    assert result["documents"] == {role: None for role in DOCUMENT_ROLES}


def test_valid_document_survives_two_failures(tmp_path):
    paths = write_valid_documents(tmp_path)
    paths[0].write_bytes(b"\xc3\x28")      # budget: invalid UTF-8
    paths[2].unlink()                       # narrative: missing

    result = intake(paths)

    assert_failed_intake(result, [
        {"document": "budget", "reason": "unreadable"},
        {"document": "project_narrative", "reason": "missing"},
    ])
    justification = result["documents"]["budget_justification"]
    assert justification["filename"] == "budget_justification.txt"
    assert justification["text"] == "Sample budget_justification text.\n"
    assert justification["sha256"] == hashlib.sha256(paths[1].read_bytes()).hexdigest()


# --- Invalid application IDs -------------------------------------------------


@pytest.mark.parametrize(
    "bad_id", ["", "   ", None, 123, b"GC-S01"], ids=["empty", "whitespace", "none", "int", "bytes"]
)
def test_invalid_application_id_raises_value_error(tmp_path, bad_id):
    paths = write_valid_documents(tmp_path)
    with pytest.raises(ValueError, match="application_id"):
        intake(paths, bad_id)


def test_invalid_application_id_is_checked_before_documents(tmp_path):
    # A caller mistake is reported as such even if the documents are also bad.
    missing = [tmp_path / "missing.txt"] * 3
    with pytest.raises(ValueError, match="application_id"):
        intake(missing, "")
