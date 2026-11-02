"""Tests for the GrantCheck command-line demonstration.

Most tests run the real entry point, `python -m grantcheck.cli`, in a
subprocess with an explicit PYTHONPATH and an unrelated working directory, so
they do not depend on pytest collection order or the current directory. A few
call main() in-process to simulate failures the fixed sample scenarios cannot.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
import grantcheck.cli as cli  # noqa: E402
from grantcheck.provenance import find_occurrences  # noqa: E402

SAMPLE_DATA_DIR = ROOT / "sample_data"
SCENARIO_IDS = ["S01", "S02", "S03", "S04", "S05", "S06", "S07"]
DISCLAIMER = (
    "GrantCheck provides candidate findings for human review. It does not approve, "
    "reject, or determine grant eligibility."
)
DETAILS_NOTICE = "potentially sensitive review information"


def run_cli(*args, cwd):
    """Run `python -m grantcheck.cli` in a fresh interpreter with only src/ added to the path."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "grantcheck.cli", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


def scenario_dir(scenario_id):
    return SAMPLE_DATA_DIR / cli.SCENARIO_DIRECTORIES[scenario_id]


def expected_results(scenario_id):
    return json.loads((scenario_dir(scenario_id) / "expected_results.json").read_text(encoding="utf-8"))


def field_value(output, label):
    match = re.search(rf"^{re.escape(label)}:\s+(.+)$", output, re.MULTILINE)
    assert match, f"{label!r} line not found in:\n{output}"
    return match.group(1).strip()


def rule_outcomes(output):
    """{"R-01": "pass", ...} parsed from the 'Rule outcomes' lines."""
    return {
        f"R-{number}": outcome
        for number, outcome in re.findall(r"^  R(\d\d)  (\S+)\s+\S", output, re.MULTILINE)
    }


# --- Scenario mapping ---------------------------------------------------------


def test_scenario_mapping_covers_seven_existing_folders():
    assert list(cli.SCENARIO_DIRECTORIES) == SCENARIO_IDS
    for scenario_id in SCENARIO_IDS:
        folder = scenario_dir(scenario_id)
        assert folder.is_dir(), f"{scenario_id}: {folder} does not exist"
        assert folder.name.startswith(f"{scenario_id}_")
        assert expected_results(scenario_id)["application_id"] == f"GC-{scenario_id}"


def test_project_root_comes_from_module_location():
    assert cli.SAMPLE_DATA_DIR == SAMPLE_DATA_DIR


# --- All seven scenarios through the entry point -------------------------------


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_summary_matches_expected_results(tmp_path, scenario_id):
    expected = expected_results(scenario_id)

    completed = run_cli("--scenario", scenario_id, cwd=tmp_path)
    output = completed.stdout

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert field_value(output, "Application ID") == f"GC-{scenario_id}"
    assert field_value(output, "Processing status") == expected["expected_processing_status"]
    assert field_value(output, "Human review required") == ("yes" if expected["expected_review_required"] else "no")
    assert field_value(output, "Finding count") == str(expected["expected_finding_count"])
    assert rule_outcomes(output) == expected["expected_rule_outcomes"]
    assert output.rstrip().endswith(DISCLAIMER)
    assert "Traceback" not in output


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_summary_hides_details(tmp_path, scenario_id):
    output = run_cli("--scenario", scenario_id, cwd=tmp_path).stdout

    assert DETAILS_NOTICE not in output
    assert "Evidence:" not in output
    assert "excerpt:" not in output


def test_summary_shows_readable_rule_names(tmp_path):
    output = run_cli("--scenario", "S01", cwd=tmp_path).stdout
    for name in (
        "Personnel cost consistency",
        "Personnel effort consistency",
        "Project duration consistency",
        "Travel justification required for positive budget travel cost",
    ):
        assert name in output


def test_findings_still_exit_zero(tmp_path):
    completed = run_cli("--scenario", "S02", cwd=tmp_path)
    assert completed.returncode == 0
    assert field_value(completed.stdout, "Finding count") == "1"


# --- --details and evidence visibility -----------------------------------------


def test_details_show_messages_roles_values_and_excerpts(tmp_path):
    completed = run_cli("--scenario", "S02", "--details", cwd=tmp_path)
    output = completed.stdout

    assert completed.returncode == 0, completed.stderr
    assert DETAILS_NOTICE in output
    assert "Message: Personnel cost consistency: Potential inconsistency" in output
    assert "personnel_cost_usd / budget: status=present, value=24000" in output
    assert "personnel_cost_usd / budget_justification: status=present, value=20000" in output
    assert 'excerpt: "Personnel Cost: $24,000"' in output
    assert 'excerpt: "Research Assistant is $20,000."' in output
    assert output.rstrip().endswith(DISCLAIMER)


def test_details_never_print_full_source_documents(tmp_path):
    output = run_cli("--scenario", "S02", "--details", cwd=tmp_path).stdout

    for name in cli.DOCUMENT_FILES:
        text = (scenario_dir("S02") / name).read_text(encoding="utf-8")
        assert text.strip() not in output, f"full text of {name} was printed"
    # Headings and lines that are not used as evidence never appear.
    assert "PERSONNEL JUSTIFICATION" not in output
    assert "monitoring locations, collect baseline water quality" not in output


def test_details_show_null_values_for_missing_records(tmp_path):
    output = run_cli("--scenario", "S05", "--details", cwd=tmp_path).stdout

    assert "travel_justification / budget_justification: status=missing, value=null" in output
    assert "excerpt: null" in output


# --- S07: expected processing exception ------------------------------------------


def test_s07_shows_structured_error_and_exits_zero(tmp_path):
    completed = run_cli("--scenario", "S07", cwd=tmp_path)
    output = completed.stdout

    assert completed.returncode == 0, "an expected processing exception is still a completed review run"
    assert field_value(output, "Processing status") == "exception"
    assert field_value(output, "Human review required") == "yes"
    assert "  - [rule_evaluation] rules_not_evaluated: R01, R02, R03, R04" in output
    assert set(rule_outcomes(output).values()) == {"not_evaluated"}


def test_s07_details_do_not_print_unreadable_placeholder(tmp_path):
    output = run_cli("--scenario", "S07", "--details", cwd=tmp_path).stdout

    assert "status=unprocessable, value=null" in output
    assert "UNREADABLE DOCUMENT CONTENT" not in output


# --- Usage errors ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [["--scenario", "S08"], ["--scenario", "s01"], ["--scenario", "S1"], ["--scenario", ""], []],
    ids=["unknown", "lowercase", "missing_digit", "empty", "no_scenario"],
)
def test_invalid_scenario_is_a_usage_error(tmp_path, args):
    completed = run_cli(*args, cwd=tmp_path)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "--scenario" in completed.stderr
    assert DISCLAIMER in completed.stderr
    assert "Traceback" not in completed.stderr


def test_invalid_scenario_lists_valid_choices(tmp_path):
    stderr = run_cli("--scenario", "S08", cwd=tmp_path).stderr
    assert "invalid choice" in stderr
    for scenario_id in SCENARIO_IDS:
        assert scenario_id in stderr


def test_help_documents_exit_codes_and_disclaimer(tmp_path):
    completed = run_cli("--help", cwd=tmp_path)
    help_text = " ".join(completed.stdout.split())  # argparse wraps lines

    assert completed.returncode == 0
    assert "Exit codes: 0 = review ran" in help_text
    assert "does not approve, reject, or determine grant eligibility" in help_text


# --- Failure paths, in-process -------------------------------------------------------


def test_replay_failure_prints_only_sanitized_error(tmp_path, monkeypatch, capsys):
    # A copy of S01 whose replay quotes a canary excerpt that is not in the budget.
    sample_copy = tmp_path / "sample_data"
    shutil.copytree(scenario_dir("S01"), sample_copy / cli.SCENARIO_DIRECTORIES["S01"])
    replay_path = sample_copy / cli.SCENARIO_DIRECTORIES["S01"] / "replay_facts.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    replay["candidate_facts"]["travel_cost_usd"]["budget"]["evidence_text"] = "CANARY-5150 private note"
    replay_path.write_text(json.dumps(replay), encoding="utf-8")
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    exit_code = cli.main(["--scenario", "S01", "--details"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert field_value(output, "Processing status") == "exception"
    assert "  - [replay] invalid_replay: Replay data could not be validated." in output
    assert "not available (processing stopped before rule evaluation)" in output
    assert "CANARY-5150" not in output


def test_intake_failure_prints_document_roles_not_text(tmp_path, monkeypatch, capsys):
    sample_copy = tmp_path / "sample_data"
    folder = sample_copy / cli.SCENARIO_DIRECTORIES["S01"]
    shutil.copytree(scenario_dir("S01"), folder)
    (folder / "budget.txt").write_bytes(b"")  # present but empty, so it reaches intake
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    exit_code = cli.main(["--scenario", "S01"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "  - [intake] empty: budget document" in output
    assert "Graduate Research Assistant" not in output
    assert output.rstrip().endswith(DISCLAIMER)


# --- Missing fixtures: checked before the orchestrator runs ------------------------


@pytest.fixture
def orchestrator_must_not_run(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("process_application must not be called when fixtures are missing")

    monkeypatch.setattr(cli, "process_application", fail)


def assert_missing_fixture_error(exit_code, captured, displayed_path):
    assert exit_code == 1
    assert captured.out == ""
    assert f"fixture not found: {displayed_path}." in captured.err
    assert "No review report was produced." in captured.err
    assert DISCLAIMER in captured.err
    assert "Traceback" not in captured.err


def test_missing_scenario_directory(tmp_path, monkeypatch, capsys, orchestrator_must_not_run):
    empty_sample_data = tmp_path / "sample_data"
    empty_sample_data.mkdir()
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", empty_sample_data)

    exit_code = cli.main(["--scenario", "S01"])

    assert_missing_fixture_error(exit_code, capsys.readouterr(), "sample_data/S01_consistent")


def test_missing_directory_is_shown_relative_to_the_repository(monkeypatch, capsys, orchestrator_must_not_run):
    # Points S01 at a folder that does not exist in the real sample_data/; nothing real is touched.
    monkeypatch.setattr(cli, "SCENARIO_DIRECTORIES", {"S01": "S01_does_not_exist"})

    exit_code = cli.main(["--scenario", "S01"])
    captured = capsys.readouterr()

    assert_missing_fixture_error(exit_code, captured, "sample_data/S01_does_not_exist")
    assert str(ROOT) not in captured.err, "the absolute repository path should not be printed"


@pytest.mark.parametrize("file_name", ["budget.txt", "budget_justification.txt", "project_narrative.txt", "replay_facts.json"])
def test_missing_required_fixture_file(tmp_path, monkeypatch, capsys, orchestrator_must_not_run, file_name):
    sample_copy = tmp_path / "sample_data"
    folder = sample_copy / cli.SCENARIO_DIRECTORIES["S01"]
    shutil.copytree(scenario_dir("S01"), folder)
    (folder / file_name).unlink()
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    exit_code = cli.main(["--scenario", "S01"])
    captured = capsys.readouterr()

    assert_missing_fixture_error(exit_code, captured, f"sample_data/S01_consistent/{file_name}")
    assert "Graduate Research Assistant" not in captured.err, "no document content may be printed"


def test_fixture_that_is_not_a_regular_file(tmp_path, monkeypatch, capsys, orchestrator_must_not_run):
    sample_copy = tmp_path / "sample_data"
    folder = sample_copy / cli.SCENARIO_DIRECTORIES["S01"]
    shutil.copytree(scenario_dir("S01"), folder)
    (folder / "replay_facts.json").unlink()
    (folder / "replay_facts.json").mkdir()
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    exit_code = cli.main(["--scenario", "S01"])

    assert_missing_fixture_error(exit_code, capsys.readouterr(), "sample_data/S01_consistent/replay_facts.json")


def test_all_missing_files_are_listed(tmp_path, monkeypatch, capsys, orchestrator_must_not_run):
    sample_copy = tmp_path / "sample_data"
    folder = sample_copy / cli.SCENARIO_DIRECTORIES["S01"]
    shutil.copytree(scenario_dir("S01"), folder)
    (folder / "budget.txt").unlink()
    (folder / "replay_facts.json").unlink()
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    exit_code = cli.main(["--scenario", "S01"])

    assert_missing_fixture_error(
        exit_code, capsys.readouterr(),
        "sample_data/S01_consistent/budget.txt, sample_data/S01_consistent/replay_facts.json",
    )


def test_unexpected_error_exits_one_without_traceback_or_detail(monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError("SECRET-DETAIL-9090")

    monkeypatch.setattr(cli, "process_application", broken)

    exit_code = cli.main(["--scenario", "S01"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "failed unexpectedly (RuntimeError)" in captured.err
    assert "SECRET-DETAIL-9090" not in captured.err
    assert "Traceback" not in captured.err
    assert DISCLAIMER in captured.err


# --- Provenance in --details (Stage 3) -----------------------------------------------

LOCATION_NOTICE = "Source locations: lines are 1-based; offsets are 0-based character positions, end exclusive."


def short_hash(scenario_id, filename):
    return hashlib.sha256((scenario_dir(scenario_id) / filename).read_bytes()).hexdigest()[:12]


def expected_location(scenario_id, filename, excerpt, line, label="location"):
    """The exact location line, with offsets computed from the file's own (checkout-dependent) text."""
    text = (scenario_dir(scenario_id) / filename).read_bytes().decode("utf-8")
    start = text.index(excerpt)
    return (
        f"        {label}: {filename} (sha256 {short_hash(scenario_id, filename)}...), "
        f"line {line}, offsets {start}-{start + len(excerpt)}"
    )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_summary_contains_no_provenance(tmp_path, scenario_id):
    output = run_cli("--scenario", scenario_id, cwd=tmp_path).stdout

    for marker in (LOCATION_NOTICE, "sha256", "offsets", "location", "source document"):
        assert marker not in output


def test_details_show_source_filenames_and_short_hashes(tmp_path):
    output = run_cli("--scenario", "S01", "--details", cwd=tmp_path).stdout

    assert LOCATION_NOTICE in output
    for filename in cli.DOCUMENT_FILES:
        assert f"{filename} (sha256 {short_hash('S01', filename)}...)" in output
        full_hash = hashlib.sha256((scenario_dir("S01") / filename).read_bytes()).hexdigest()
        assert full_hash not in output, "only an abbreviated fingerprint is shown"


def test_s02_details_locate_both_conflicting_costs(tmp_path):
    lines = run_cli("--scenario", "S02", "--details", cwd=tmp_path).stdout.splitlines()

    assert expected_location("S02", "budget.txt", "Personnel Cost: $24,000", 12) in lines
    assert expected_location("S02", "budget_justification.txt", "Research Assistant is $20,000.", 16) in lines


def test_location_follows_its_own_evidence_record(tmp_path):
    lines = run_cli("--scenario", "S02", "--details", cwd=tmp_path).stdout.splitlines()
    index = lines.index('        excerpt: "Research Assistant is $20,000."')
    assert lines[index + 1] == expected_location(
        "S02", "budget_justification.txt", "Research Assistant is $20,000.", 16,
    )


def test_s06_uncertain_location_is_not_presented_as_confirmed(tmp_path):
    lines = run_cli("--scenario", "S06", "--details", cwd=tmp_path).stdout.splitlines()
    excerpt = "Travel funds support project-related attendance and transportation activities."

    assert "      travel_justification / budget_justification: status=uncertain, value=null" in lines
    assert expected_location("S06", "budget_justification.txt", excerpt, 21, label="location (uncertain evidence)") in lines
    assert expected_location("S06", "budget_justification.txt", excerpt, 21) not in lines


def test_s07_details_do_not_fabricate_locations(tmp_path):
    output = run_cli("--scenario", "S07", "--details", cwd=tmp_path).stdout
    document_only = (
        f"        source document: budget_justification.txt "
        f"(sha256 {short_hash('S07', 'budget_justification.txt')}...); no evidence location"
    )

    # R01-R04 each cite one unprocessable justification record.
    assert output.splitlines().count(document_only) == 4
    assert "budget_justification.txt (sha256" not in output.replace(document_only, "")
    assert expected_location("S07", "budget.txt", "Personnel Cost: $24,000", 12) in output.splitlines()


def test_missing_evidence_shows_document_identity_only(tmp_path):
    lines = run_cli("--scenario", "S05", "--details", cwd=tmp_path).stdout.splitlines()
    index = lines.index("      travel_justification / budget_justification: status=missing, value=null")

    assert lines[index + 1] == "        excerpt: null"
    assert lines[index + 2].startswith("        source document: budget_justification.txt (sha256 ")
    assert lines[index + 2].endswith("; no evidence location")


def test_replay_failure_details_show_no_locations(tmp_path, monkeypatch, capsys):
    sample_copy = tmp_path / "sample_data"
    folder = sample_copy / cli.SCENARIO_DIRECTORIES["S01"]
    shutil.copytree(scenario_dir("S01"), folder)
    (folder / "replay_facts.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(cli, "SAMPLE_DATA_DIR", sample_copy)

    assert cli.main(["--scenario", "S01", "--details"]) == 0
    output = capsys.readouterr().out

    assert "  - [replay] invalid_replay: Replay data could not be validated." in output
    assert "sha256" not in output
    assert "offsets" not in output


# Synthetic results for cases the fixtures do not contain (repeated excerpts, >100 matches).


def synthetic_result(provenance, statuses=("present", "uncertain")):
    evidence = {
        "personnel_role": {
            "budget": {"value": "x", "status": statuses[0], "evidence_text": "x"},
            "budget_justification": {
                "value": None if statuses[1] != "present" else "x", "status": statuses[1], "evidence_text": "x",
            },
        },
    }
    return {
        "application_id": "GC-TEST",
        "processing_status": "completed",
        "review_required": True,
        "candidate_facts": None,
        "rule_results": {"R01": {"rule_name": "Test rule", "outcome": "uncertain",
                                 "message": "Test rule: test", "evidence": evidence}},
        "finding_count": 0,
        "errors": [],
        "provenance": provenance,
    }


def synthetic_source(filename, text, excerpt):
    return {"filename": filename, "document_sha256": "a" * 64, **find_occurrences(text, excerpt)}


def test_repeated_and_over_limit_occurrences_are_not_presented_as_unique():
    provenance = {"personnel_role": {
        "budget": synthetic_source("budget.txt", "x x\nx", "x"),                     # 3 occurrences
        "budget_justification": synthetic_source("budget_justification.txt", "x\n" * 250, "x"),  # 250
    }}

    lines = cli.format_report("S01", synthetic_result(provenance), details=True).splitlines()

    three = lines.index(
        "        possible locations: budget.txt (sha256 aaaaaaaaaaaa...); "
        "excerpt occurs 3 times, not uniquely identified"
    )
    assert lines[three + 1:three + 4] == [
        "          - line 1, offsets 0-1",
        "          - line 1, offsets 2-3",
        "          - line 2, offsets 4-5",
    ]
    many = lines.index(
        "        possible locations (uncertain evidence): budget_justification.txt (sha256 aaaaaaaaaaaa...); "
        "excerpt occurs 250 times, not uniquely identified; showing first 100"
    )
    bullets = [line for line in lines[many + 1:] if line.startswith("          - ")]
    assert len(bullets) == 100
    assert bullets[-1] == "          - line 100, offsets 198-199"


def test_multiline_location_shows_line_range():
    text = "first\nsecond\nthird"
    provenance = {"personnel_role": {
        "budget": synthetic_source("budget.txt", text, "first\nsecond"),
        "budget_justification": synthetic_source("budget_justification.txt", text, "third"),
    }}

    output = cli.format_report("S01", synthetic_result(provenance), details=True)

    assert "        location: budget.txt (sha256 aaaaaaaaaaaa...), lines 1-2, offsets 0-12" in output


@pytest.mark.parametrize("missing_how", ["none", "absent_key"])
def test_details_handle_missing_provenance_safely(missing_how):
    result = synthetic_result(None)
    if missing_how == "absent_key":
        del result["provenance"]

    lines = cli.format_report("S01", result, details=True).splitlines()

    assert lines.count("        source: not available (provenance not resolved)") == 2
    assert lines[-1] == DISCLAIMER


def test_main_with_no_provenance_still_reports(monkeypatch, capsys):
    real_process = cli.process_application

    def without_provenance(*args):
        result = real_process(*args)
        result["provenance"] = None
        return result

    monkeypatch.setattr(cli, "process_application", without_provenance)

    assert cli.main(["--scenario", "S02", "--details"]) == 0
    output = capsys.readouterr().out
    assert "source: not available (provenance not resolved)" in output
    assert 'excerpt: "Research Assistant is $20,000."' in output
