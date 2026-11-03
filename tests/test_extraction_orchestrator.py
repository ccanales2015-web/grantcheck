"""Tests for extraction-mode orchestration and initial R03 evaluation.

Synthetic documents go through real document intake. Failure paths use the
orchestrator's narrow stage injection (_process) rather than monkey-patching.
"""
import copy
import itertools
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.document_intake import DOCUMENT_ROLES, intake_application  # noqa: E402
from grantcheck.duration_extraction import extract_candidate_facts, normalize_duration_quantity  # noqa: E402
from grantcheck.duration_provenance import DiagnosticAttempt, verify_duration_extraction  # noqa: E402
from grantcheck.evaluation_snapshot import EvaluationSnapshot  # noqa: E402
from grantcheck.extraction_orchestrator import ExtractionRun, _process, process_extracted_application  # noqa: E402
from grantcheck.orchestrator import process_application  # noqa: E402
from grantcheck.processing_errors import validate_processing_errors  # noqa: E402
from grantcheck.rule_engine import evaluate_duration_rule, evaluate_rules  # noqa: E402

RUN = "12345678-1234-4234-8234-123456789abc"
OTHER_RUN = "87654321-4321-4321-8321-cba987654321"
DURATION = "project_duration_months"
FILENAMES = {"budget": "budget.txt", "budget_justification": "budget_justification.txt",
             "project_narrative": "project_narrative.txt"}
SUCCESS_KEYS = {"processing_run_id", "application_id", "processing_status", "failure_code", "review_required",
                "candidate_facts", "rule_results", "processing_errors", "not_found_reasons", "verified_evidence"}
FAILURE_KEYS = {"processing_run_id", "failure_code", "rule_results", "diagnostics"}
CANARY = "CANARY-a17c"


def run_factory():
    return uuid.UUID(RUN)


def sequential_attempt_ids():
    counter = itertools.count(1)
    return lambda: uuid.UUID(int=next(counter))


@pytest.fixture
def make_application(tmp_path):
    counter = itertools.count()

    def make(budget="Project Duration: 24 months", justification="Duration: 2 years",
             narrative="Duration: 24 months", newline="\n"):
        folder = tmp_path / f"app{next(counter)}"
        folder.mkdir()
        paths = []
        for role, line in zip(DOCUMENT_ROLES, (budget, justification, narrative)):
            path = folder / FILENAMES[role]
            path.write_bytes(newline.join([role.upper(), line, "End."]).encode("utf-8") + newline.encode())
            paths.append(path)
        application = intake_application("GC-TEST", *paths)
        assert application["intake_status"] == "completed", "test setup: intake failed"
        return application
    return make


def run(application, **stages):
    return _process(application, run_factory, sequential_attempt_ids(), **stages)


def r03(result):
    return result.response["rule_results"]["R03"]


def corrupting_extract(role, **changes):
    """The real extractor, with one recorded occurrence altered afterwards."""
    def extract(application):
        extraction = extract_candidate_facts(application)
        extraction["duration_evidence"][role][0].update(changes)
        return extraction
    return extract


# --- Successful runs ----------------------------------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_clean_three_document_match(make_application, newline):
    result = run(make_application(newline=newline))
    response = result.response

    assert set(response) == SUCCESS_KEYS
    assert response["processing_run_id"] == RUN
    assert response["application_id"] == "GC-TEST"
    assert response["processing_status"] == "completed"
    assert response["failure_code"] is None
    assert r03(result)["outcome"] == "pass"
    assert response["review_required"] is False
    assert response["processing_errors"] == []
    assert [response["candidate_facts"][DURATION][role]["value"] for role in DOCUMENT_ROLES] == [24, 24, 24]
    assert isinstance(result.snapshot, EvaluationSnapshot)
    assert result.diagnostic_attempts == ()


@pytest.mark.parametrize("budget, justification, narrative, outcome", [
    ("Project Duration: 24 months", "Duration: 3 years", "Duration: 24 months", "finding"),
    ("Project Duration: 24 months", "Duration: 36 months", "The project runs for two years.", "finding"),
    ("BUDGET NOTES", "No duration here.", "Over two years of work.", "uncertain"),
    ("Project Duration: 24 months", "Duration: 2 years", "Duration: about two years", "uncertain"),
    ("Project Duration: 24 months", "Duration: 24 months", "Duration: 24 months\nDuration: 3 years", "uncertain"),
], ids=["verified_conflict", "conflict_with_third_not_found", "no_statements", "ambiguous_expression",
        "conflict_within_one_document"])
def test_r03_outcomes_from_extracted_facts(make_application, budget, justification, narrative, outcome):
    result = run(make_application(budget, justification, narrative))
    assert r03(result)["outcome"] == outcome
    assert result.response["review_required"] is True
    assert result.snapshot is not None


def test_missing_statements_have_separate_reason_codes(make_application):
    response = run(make_application("BUDGET NOTES", "No duration.", "Prose only.")).response
    assert response["not_found_reasons"][DURATION] == {role: "no_supported_pattern_matched" for role in DOCUMENT_ROLES}
    assert response["not_found_reasons"]["personnel_cost_usd"]["budget"] == "unsupported_fact_type"


def test_isolated_provenance_failure_then_safe_r03(make_application):
    result = run(make_application(), extract=corrupting_extract("budget", start_line=99))
    response = result.response

    assert response["failure_code"] is None, "an isolated failure does not end the run"
    assert response["candidate_facts"][DURATION]["budget"] == {"value": None, "status": "unprocessable",
                                                              "evidence_text": None}
    assert response["verified_evidence"]["budget"] == []
    assert len(response["verified_evidence"]["budget_justification"]) == 1
    assert response["processing_errors"] == [{"error_type": "extraction_failure", "document": "budget",
                                              "fact_category": DURATION, "processing_run_id": RUN}]
    assert r03(result)["outcome"] == "not_evaluated"       # 24 and 24 agree, budget unprocessable
    assert r03(result)["evidence"][DURATION]["budget"]["status"] == "unprocessable"   # isolated facts used
    assert response["review_required"] is True
    assert [a.reason for a in result.diagnostic_attempts] == ["line_mismatch"]


def test_confirmed_conflict_despite_unprocessable_input(make_application):
    application = make_application("Project Duration: 24 months", "Duration: 3 years", "Duration: 24 months")
    result = run(application, extract=corrupting_extract("project_narrative", excerpt="forged"))

    assert result.response["candidate_facts"][DURATION]["project_narrative"]["status"] == "unprocessable"
    assert r03(result)["outcome"] == "finding"
    assert "Budget: 24 months; Budget Justification: 36 months" in r03(result)["message"]


def test_processing_error_with_a_clean_pass_still_requires_review(make_application):
    def claim_cost(application):
        extraction = extract_candidate_facts(application)
        extraction["candidate_facts"]["personnel_cost_usd"]["budget"] = {
            "value": 1, "status": "present", "evidence_text": "BUDGET"}
        del extraction["not_found_reasons"]["personnel_cost_usd"]["budget"]
        return extraction

    result = run(make_application(), extract=claim_cost)
    assert r03(result)["outcome"] == "pass"
    assert result.response["processing_errors"][0]["fact_category"] == "personnel_cost_usd"
    assert result.response["review_required"] is True


# --- R03 only ---------------------------------------------------------------------


def test_only_r03_is_evaluated(make_application):
    response = run(make_application()).response
    assert set(response["rule_results"]) == {"R03"}
    assert set(response["rule_results"]["R03"]) == {"rule_name", "outcome", "message", "evidence"}


@pytest.mark.parametrize("scenario", sorted(p.name for p in (ROOT / "sample_data").glob("S0*_*")))
def test_duration_rule_matches_full_rule_set(scenario):
    facts = json.loads((ROOT / "sample_data" / scenario / "replay_facts.json").read_text(encoding="utf-8"))["candidate_facts"]
    assert evaluate_duration_rule(facts) == {"R03": evaluate_rules(facts)["R03"]}


def test_shared_normalization_helper():
    assert normalize_duration_quantity(" 2 years ") == 24
    assert normalize_duration_quantity("24 Months.") == 24
    assert normalize_duration_quantity("about two years") is None
    assert normalize_duration_quantity("0 months") is None


# --- Review policy ----------------------------------------------------------------


@pytest.mark.parametrize("narrative, extract_changes, outcome, review", [
    ("Duration: 24 months", None, "pass", False),
    ("Duration: 3 years", None, "finding", True),
    ("Duration: two years", None, "uncertain", True),
    ("Duration: 24 months", {"start_line": 99}, "not_evaluated", True),
], ids=["pass", "finding", "uncertain", "not_evaluated"])
def test_review_required_per_outcome(make_application, narrative, extract_changes, outcome, review):
    stages = {"extract": corrupting_extract("project_narrative", **extract_changes)} if extract_changes else {}
    result = run(make_application(narrative=narrative), **stages)
    assert r03(result)["outcome"] == outcome
    assert result.response["review_required"] is review


# --- Controlled failures ----------------------------------------------------------


def assert_failure(result, code, run_id=RUN):
    assert isinstance(result, ExtractionRun)
    assert set(result.response) == FAILURE_KEYS
    assert result.response["failure_code"] == code
    assert result.response["processing_run_id"] == run_id
    assert result.response["rule_results"] is None
    assert result.snapshot is None, "no snapshot without a completed evaluation"
    json.dumps(result.response)


def must_not_evaluate(candidate_facts):
    raise AssertionError("R03 must not be evaluated")


def test_uuid_initialization_failure(make_application):
    def broken():
        raise OSError(CANARY)

    result = _process(make_application(), broken, sequential_attempt_ids(), evaluate=must_not_evaluate)

    assert_failure(result, "fatal_internal_failure", run_id=None)
    assert result.response["diagnostics"] == []
    assert CANARY not in json.dumps(result.response)


def test_diagnostic_attempt_uuid_failure(make_application):
    def broken():
        raise OSError(CANARY)

    result = _process(make_application(), run_factory, broken,
                      extract=corrupting_extract("budget", start_line=99), evaluate=must_not_evaluate)
    assert_failure(result, "fatal_internal_failure")
    assert CANARY not in json.dumps(result.response)


def test_invalid_processing_error_records_prevent_evaluation(make_application):
    def tampered_verify(application, extraction, run_id, factory):
        verification = verify_duration_extraction(application, extraction, run_id, factory)
        verification["processing_errors"][0]["processing_run_id"] = OTHER_RUN
        verification["processing_errors"][0]["document"] = CANARY
        return verification

    result = run(make_application(), extract=corrupting_extract("budget", start_line=99),
                 verify=tampered_verify, evaluate=must_not_evaluate)

    assert_failure(result, "invalid_processing_error_record")
    assert result.response["diagnostics"] == [
        {"record_index": 0, "field": "document", "reason": "unsupported_identifier"},
        {"record_index": 0, "field": "processing_run_id", "reason": "run_identity_mismatch"},
    ]
    assert CANARY not in json.dumps(result.response) and OTHER_RUN not in json.dumps(result.response)
    assert len(result.diagnostic_attempts) == 1, "restricted attempts stay available internally"


def test_validator_runs_before_the_integrity_decision(make_application):
    seen = []

    def recording_validator(records, run_id):
        seen.append("validated")
        return validate_processing_errors(records, run_id)

    result = run(make_application(), extract=lambda application: {"malformed": True},
                 validate_errors=recording_validator, evaluate=must_not_evaluate)

    assert seen == ["validated"]
    assert_failure(result, "application_integrity_failure")
    assert [a.reason for a in result.diagnostic_attempts] == ["application_integrity"]


@pytest.mark.parametrize("tamper", [
    lambda app: app["documents"]["budget"].update(text=app["documents"]["budget"]["text"] + "x"),
    lambda app: app["documents"]["budget"].update(sha256="0" * 64),
    lambda app: app["documents"]["budget"].update(filename="folder/budget.txt"),
    lambda app: app.update(intake_status="exception"),
    lambda app: app["documents"].update(project_narrative=None),
    lambda app: app.update(application_id="  "),
    lambda app: app.pop("documents"),
], ids=["text_altered", "hash_altered", "path_filename", "intake_exception", "failed_document",
        "blank_application_id", "no_documents"])
def test_untrustworthy_intake_is_an_integrity_failure(make_application, tamper):
    application = make_application()
    tamper(application)
    assert_failure(run(application, evaluate=must_not_evaluate), "application_integrity_failure")


@pytest.mark.parametrize("application", [None, [], "GC-TEST"], ids=repr)
def test_non_record_application_is_an_integrity_failure(application):
    assert_failure(_process(application, run_factory, None), "application_integrity_failure")


@pytest.mark.parametrize("stage", ["extract", "verify", "validate_errors", "evaluate"])
def test_unexpected_internal_failure_is_sanitized(make_application, stage):
    def explode(*args):
        raise RuntimeError(f"{CANARY} Traceback: secret")

    result = run(make_application(), **{stage: explode})

    assert_failure(result, "fatal_internal_failure")
    serialized = json.dumps(result.response)
    assert CANARY not in serialized and "Traceback" not in serialized


# --- Run identity -----------------------------------------------------------------


def test_one_authoritative_uuid_per_execution(make_application):
    calls = []

    def counting_factory():
        calls.append(1)
        return uuid.UUID(RUN)

    application = make_application()
    application["processing_run_id"] = OTHER_RUN           # never accepted as authoritative
    result = _process(application, counting_factory, sequential_attempt_ids(),
                      extract=corrupting_extract("budget", start_line=99))

    assert len(calls) == 1
    assert result.response["processing_run_id"] == RUN
    assert {error["processing_run_id"] for error in result.response["processing_errors"]} == {RUN}
    assert result.snapshot.processing_run_id == RUN
    assert {attempt.processing_run_id for attempt in result.diagnostic_attempts} == {RUN}


def test_default_factories_create_fresh_ids(make_application):
    application = make_application()
    first = process_extracted_application(application).response["processing_run_id"]
    second = process_extracted_application(application).response["processing_run_id"]
    assert first != second
    assert str(uuid.UUID(first)) == first


# --- Snapshot and public response -------------------------------------------------


def test_snapshot_matches_the_completed_evaluation(make_application):
    result = run(make_application(), extract=corrupting_extract("budget", start_line=99))
    snapshot = result.snapshot.to_dict()
    response = result.response

    assert snapshot["processing_run_id"] == RUN
    assert snapshot["trigger"] == "initial_extraction_evaluation"
    assert snapshot["sequence"] == 0 and snapshot["rule_id"] == "R03"
    assert snapshot["candidate_facts"] == response["candidate_facts"]
    assert snapshot["verified_evidence"] == response["verified_evidence"]
    assert snapshot["rule_result"] == response["rule_results"]["R03"]
    assert snapshot["processing_errors"] == response["processing_errors"]


def test_snapshot_is_detached_from_application_and_response(make_application):
    application = make_application()
    result = run(application)
    before = result.snapshot.to_dict()

    result.response["candidate_facts"][DURATION]["budget"]["value"] = 99
    result.response["rule_results"]["R03"]["outcome"] = "finding"
    application["documents"]["budget"]["text"] = "changed"

    assert result.snapshot.to_dict() == before


def test_public_response_is_json_and_excludes_restricted_material(make_application):
    result = run(make_application(), extract=corrupting_extract("budget", excerpt=f"{CANARY} forged"))
    serialized = json.dumps(result.response)

    assert CANARY not in serialized
    assert "attempt_id" not in serialized and "diagnostic" not in serialized
    assert not any(isinstance(value, (DiagnosticAttempt, EvaluationSnapshot)) for value in result.response.values())
    assert any(CANARY in json.dumps(a.to_export_dict()) for a in result.diagnostic_attempts)
    assert "BUDGET\nProject Duration" not in serialized, "no raw source documents"


def test_repr_does_not_reveal_contents(make_application):
    result = run(make_application(), extract=corrupting_extract("budget", excerpt=f"{CANARY} forged"))
    assert CANARY not in repr(result)


def test_results_are_deterministic_with_injected_factories(make_application):
    application = make_application(narrative="Duration: 3 years")
    first, second = run(application), run(application)
    assert first.response == second.response
    assert first.snapshot.to_dict() == second.snapshot.to_dict()


def test_application_is_not_modified(make_application):
    application = make_application()
    before = copy.deepcopy(application)
    run(application, extract=corrupting_extract("budget", start_line=99))
    assert application == before


# --- Replay workflow unchanged ----------------------------------------------------


@pytest.mark.parametrize("scenario", sorted(p.name for p in (ROOT / "sample_data").glob("S0*_*")))
def test_replay_workflow_is_unchanged(scenario):
    folder = ROOT / "sample_data" / scenario
    expected = json.loads((folder / "expected_results.json").read_text(encoding="utf-8"))
    result = process_application(expected["application_id"], *(folder / FILENAMES[r] for r in DOCUMENT_ROLES),
                                 folder / "replay_facts.json")

    assert set(result) == {"application_id", "processing_status", "review_required", "candidate_facts",
                           "rule_results", "finding_count", "errors", "provenance"}
    assert set(result["rule_results"]) == {"R01", "R02", "R03", "R04"}
    assert {f"R-{k[1:]}": v["outcome"] for k, v in result["rule_results"].items()} == expected["expected_rule_outcomes"]
    assert result["processing_status"] == expected["expected_processing_status"]
