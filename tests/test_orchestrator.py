"""Offline, deterministic tests for the GrantCheck application orchestrator.

The real sample_data/ files are only read; failure cases use copies in tmp_path.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import grantcheck.orchestrator as orchestrator  # noqa: E402
from grantcheck.fact_validation import CandidateFactValidationError  # noqa: E402
from grantcheck.orchestrator import process_application  # noqa: E402

SAMPLE_DATA_DIR = ROOT / "sample_data"
SCENARIOS = [
    "S01_consistent",
    "S02_personnel_cost_conflict",
    "S03_personnel_effort_conflict",
    "S04_duration_conflict",
    "S05_missing_travel_justification",
    "S06_ambiguous_travel_justification",
    "S07_unprocessable_document",
]
DOCUMENT_FILES = ("budget.txt", "budget_justification.txt", "project_narrative.txt")
# The exact public errors; validation details must never appear in results.
REPLAY_ERROR = {
    "stage": "replay", "reason": "invalid_replay", "message": "Replay data could not be validated.",
}
CANDIDATE_FACT_ERROR = {
    "stage": "rule_evaluation", "reason": "invalid_candidate_facts",
    "message": "Candidate facts could not be validated.",
}
RESULT_KEYS = {
    "application_id", "processing_status", "review_required",
    "candidate_facts", "rule_results", "finding_count", "errors",
}


def expected_results(scenario_dir):
    return json.loads((scenario_dir / "expected_results.json").read_text(encoding="utf-8"))


def replay_facts(scenario_dir):
    return json.loads((scenario_dir / "replay_facts.json").read_text(encoding="utf-8"))["candidate_facts"]


SCENARIO_ID = object()  # default: use the scenario's own application ID


def run(scenario_dir, application_id=SCENARIO_ID, replay_path=None):
    if application_id is SCENARIO_ID:
        application_id = expected_results(scenario_dir)["application_id"]
    return process_application(
        application_id,
        *(scenario_dir / name for name in DOCUMENT_FILES),
        replay_path if replay_path is not None else scenario_dir / "replay_facts.json",
    )


def outcomes(result):
    """Rule outcomes keyed like expected_results.json ("R-01"), from the engine's "R01"."""
    return {f"{rule_id[0]}-{rule_id[1:]}": rule["outcome"] for rule_id, rule in result["rule_results"].items()}


@pytest.fixture
def s01_copy(tmp_path):
    """A writable copy of S01 for failure cases."""
    return Path(shutil.copytree(SAMPLE_DATA_DIR / "S01_consistent", tmp_path / "S01_consistent"))


# --- The seven scenarios -------------------------------------------------------


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_scenario_matches_expected_results(scenario_name):
    scenario_dir = SAMPLE_DATA_DIR / scenario_name
    expected = expected_results(scenario_dir)

    result = run(scenario_dir)

    assert set(result) == RESULT_KEYS
    assert result["application_id"] == expected["application_id"]
    assert result["processing_status"] == expected["expected_processing_status"]
    assert result["review_required"] is expected["expected_review_required"]
    assert result["finding_count"] == expected["expected_finding_count"]
    assert outcomes(result) == expected["expected_rule_outcomes"]
    assert result["candidate_facts"] == replay_facts(scenario_dir)


@pytest.mark.parametrize("scenario_name", [s for s in SCENARIOS if s != "S07_unprocessable_document"])
def test_completed_scenarios_have_no_errors(scenario_name):
    result = run(SAMPLE_DATA_DIR / scenario_name)
    assert result["processing_status"] == "completed"
    assert result["errors"] == []


def test_s01_all_pass_needs_no_review():
    result = run(SAMPLE_DATA_DIR / "S01_consistent")
    assert set(outcomes(result).values()) == {"pass"}
    assert result["review_required"] is False


def test_s06_uncertain_needs_review_without_findings():
    result = run(SAMPLE_DATA_DIR / "S06_ambiguous_travel_justification")
    assert outcomes(result)["R-04"] == "uncertain"
    assert result["finding_count"] == 0
    assert result["processing_status"] == "completed"
    assert result["review_required"] is True


def test_s07_preserves_facts_and_not_evaluated_results():
    scenario_dir = SAMPLE_DATA_DIR / "S07_unprocessable_document"
    result = run(scenario_dir)

    assert result["processing_status"] == "exception"
    assert result["review_required"] is True
    assert result["candidate_facts"] == replay_facts(scenario_dir)
    assert set(result["rule_results"]) == {"R01", "R02", "R03", "R04"}
    assert set(outcomes(result).values()) == {"not_evaluated"}
    assert result["finding_count"] == 0
    assert result["errors"] == [
        {"stage": "rule_evaluation", "reason": "rules_not_evaluated", "rules": ["R01", "R02", "R03", "R04"]},
    ]


def test_results_are_deterministic():
    scenario_dir = SAMPLE_DATA_DIR / "S02_personnel_cost_conflict"
    assert run(scenario_dir) == run(scenario_dir)


# --- Intake failures -----------------------------------------------------------


def test_intake_failure_skips_replay_and_rules(s01_copy, monkeypatch):
    (s01_copy / "budget.txt").unlink()
    (s01_copy / "project_narrative.txt").write_bytes(b"")

    def must_not_run(*args, **kwargs):
        raise AssertionError("must not be called after an intake failure")

    monkeypatch.setattr(orchestrator, "load_replay_facts", must_not_run)
    monkeypatch.setattr(orchestrator, "evaluate_rules", must_not_run)

    result = run(s01_copy)

    assert set(result) == RESULT_KEYS
    assert result["application_id"] == "GC-S01"
    assert result["processing_status"] == "exception"
    assert result["review_required"] is True
    assert result["candidate_facts"] is None
    assert result["rule_results"] is None
    assert result["finding_count"] == 0
    assert result["errors"] == [
        {"stage": "intake", "reason": "missing", "document": "budget"},
        {"stage": "intake", "reason": "empty", "document": "project_narrative"},
    ]


def test_intake_errors_do_not_expose_document_text(s01_copy):
    (s01_copy / "budget.txt").unlink()
    justification_text = (s01_copy / "budget_justification.txt").read_text(encoding="utf-8")

    serialized = json.dumps(run(s01_copy))

    assert justification_text not in serialized
    assert "Graduate Research Assistant" not in serialized


# --- Invalid replay and rule-evaluation failures -------------------------------


def test_invalid_replay_becomes_exception_result(s01_copy):
    (s01_copy / "replay_facts.json").write_text('{"application_id": "GC-S01",', encoding="utf-8")

    result = run(s01_copy)

    assert result["processing_status"] == "exception"
    assert result["review_required"] is True
    assert result["candidate_facts"] is None
    assert result["rule_results"] is None
    assert result["finding_count"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"] == [REPLAY_ERROR]


def test_missing_replay_becomes_exception_result(s01_copy):
    (s01_copy / "replay_facts.json").unlink()

    result = run(s01_copy)

    assert result["processing_status"] == "exception"
    assert result["candidate_facts"] is None
    assert result["errors"] == [REPLAY_ERROR]


def test_replay_for_another_application_becomes_exception_result():
    result = run(SAMPLE_DATA_DIR / "S01_consistent", replay_path=SAMPLE_DATA_DIR / "S02_personnel_cost_conflict" / "replay_facts.json")

    assert result["processing_status"] == "exception"
    assert result["candidate_facts"] is None
    assert result["errors"] == [REPLAY_ERROR]
    assert "GC-S02" not in json.dumps(result), "the other application's ID must not be exposed"


def test_replay_error_does_not_expose_invalid_replay_content(s01_copy):
    # The loader's own message would quote this unverifiable evidence excerpt.
    canary = "CANARY-7731 confidential travel to Lisbon"
    replay = json.loads((s01_copy / "replay_facts.json").read_text(encoding="utf-8"))
    replay["candidate_facts"]["travel_cost_usd"]["budget"]["evidence_text"] = canary
    (s01_copy / "replay_facts.json").write_text(json.dumps(replay), encoding="utf-8")

    result = run(s01_copy)

    assert result["errors"] == [REPLAY_ERROR]
    serialized = json.dumps(result)
    assert "CANARY-7731" not in serialized
    assert "candidate_facts.travel_cost_usd" not in serialized, "field paths must not be exposed"


def test_replay_error_does_not_expose_invalid_values(s01_copy):
    replay = json.loads((s01_copy / "replay_facts.json").read_text(encoding="utf-8"))
    replay["candidate_facts"]["personnel_cost_usd"]["budget"]["value"] = "987654321-CANARY"
    (s01_copy / "replay_facts.json").write_text(json.dumps(replay), encoding="utf-8")

    result = run(s01_copy)

    assert result["errors"] == [REPLAY_ERROR]
    assert "987654321" not in json.dumps(result)


def test_rule_evaluation_failure_keeps_loaded_facts(monkeypatch):
    def reject(candidate_facts):
        raise CandidateFactValidationError("candidate_facts.travel_cost_usd.budget.value: 424242-CANARY")

    monkeypatch.setattr(orchestrator, "evaluate_rules", reject)
    scenario_dir = SAMPLE_DATA_DIR / "S01_consistent"

    result = run(scenario_dir)

    assert result["processing_status"] == "exception"
    assert result["review_required"] is True
    assert result["candidate_facts"] == replay_facts(scenario_dir)
    assert result["rule_results"] is None
    assert result["finding_count"] == 0
    assert result["errors"] == [CANDIDATE_FACT_ERROR]
    serialized = json.dumps(result["errors"])
    assert "424242" not in serialized, "the validator's message must not be exposed"
    assert "candidate_facts.travel_cost_usd" not in serialized


# --- Caller mistakes are not swallowed -----------------------------------------


@pytest.mark.parametrize("bad_id", ["", "   ", None, 123], ids=["empty", "blank", "none", "int"])
def test_invalid_application_id_raises(bad_id):
    with pytest.raises(ValueError, match="application_id"):
        run(SAMPLE_DATA_DIR / "S01_consistent", application_id=bad_id)


def test_unsupported_replay_path_type_raises():
    scenario_dir = SAMPLE_DATA_DIR / "S01_consistent"
    with pytest.raises(TypeError):
        process_application("GC-S01", *(scenario_dir / name for name in DOCUMENT_FILES), None)


def test_unsupported_document_path_type_raises():
    scenario_dir = SAMPLE_DATA_DIR / "S01_consistent"
    with pytest.raises(TypeError):
        process_application(
            "GC-S01", None, scenario_dir / "budget_justification.txt",
            scenario_dir / "project_narrative.txt", scenario_dir / "replay_facts.json",
        )


def test_unexpected_errors_are_not_swallowed(monkeypatch):
    def broken(candidate_facts):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(orchestrator, "evaluate_rules", broken)
    with pytest.raises(RuntimeError, match="unexpected bug"):
        run(SAMPLE_DATA_DIR / "S01_consistent")
