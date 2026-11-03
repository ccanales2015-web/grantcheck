"""Extraction-mode orchestration: deterministic duration extraction and R03.

process_extracted_application() runs, in order:
    1. create the authoritative processing-run UUID (never supplied by callers);
    2. check that the document-intake record is complete and intact;
    3. extract candidate facts (duration_extraction);
    4. verify extracted provenance and isolate untrusted facts (duration_provenance);
    5. validate every structured processing-error record;
    6. stop if application integrity is compromised;
    7. evaluate R03 only, on the verified and isolated candidate facts;
    8. create the immutable initial evaluation snapshot;
    9. build a sanitized, JSON-serializable public response.

Any failure before step 7 ends the run with a controlled failure response from
processing_errors.build_failure_response(); no rule is evaluated and no
snapshot is created. Restricted diagnostic attempts and snapshots are returned
only in the internal ExtractionRun, never in the public response.

GrantCheck flags matters for human review; it never approves, rejects, or
decides eligibility. The replay workflow (grantcheck.orchestrator) is separate
and unchanged.
"""

import copy
import hashlib
import uuid
from dataclasses import dataclass

from grantcheck.document_intake import DOCUMENT_ROLES, INTAKE_COMPLETED
from grantcheck.duration_extraction import extract_candidate_facts
from grantcheck.duration_provenance import verify_duration_extraction
from grantcheck.evaluation_snapshot import create_initial_snapshot
from grantcheck.processing_errors import (
    build_failure_response,
    initialize_processing_run,
    validate_processing_errors,
)
from grantcheck.rule_engine import evaluate_duration_rule

PROCESSING_COMPLETED = "completed"


@dataclass(frozen=True, repr=False)
class ExtractionRun:
    """Internal result of one extraction run.

    response is the public, JSON-serializable dict. snapshot (or None) and
    diagnostic_attempts are restricted and must not be added to the response.
    """

    response: dict
    snapshot: object = None
    diagnostic_attempts: tuple = ()

    def __repr__(self):
        return (f"ExtractionRun(processing_run_id={self.response.get('processing_run_id')!r}, "
                f"snapshot={'present' if self.snapshot is not None else 'none'}, "
                f"diagnostic_attempts={len(self.diagnostic_attempts)})")


def _application_is_intact(application):
    """True if the intake record is complete and each document's hash matches its stored text."""
    if not isinstance(application, dict):
        return False
    application_id = application.get("application_id")
    if not isinstance(application_id, str) or not application_id.strip():
        return False
    if application.get("intake_status") != INTAKE_COMPLETED or not isinstance(application.get("documents"), dict):
        return False
    for role in DOCUMENT_ROLES:
        document = application["documents"].get(role)
        if not isinstance(document, dict):
            return False
        filename, text, sha256 = document.get("filename"), document.get("text"), document.get("sha256")
        if not isinstance(filename, str) or not filename.strip() or "/" in filename or "\\" in filename:
            return False
        if not isinstance(text, str) or not isinstance(sha256, str):
            return False
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
            return False
    return True


def _failure(code, run_id, diagnostics=(), attempts=()):
    return ExtractionRun(build_failure_response(code, run_id, diagnostics), None, tuple(attempts))


def _process(application, uuid_factory, attempt_id_factory,
             extract=extract_candidate_facts, verify=verify_duration_extraction,
             validate_errors=validate_processing_errors, evaluate=evaluate_duration_rule):
    """The extraction workflow, with each stage injectable for narrow testing."""
    run_id, failure = initialize_processing_run(uuid_factory)
    if failure is not None:
        return ExtractionRun(failure)

    if not _application_is_intact(application):
        return _failure("application_integrity_failure", run_id)

    try:
        extraction = extract(application)
        verification = verify(application, extraction, run_id, attempt_id_factory or uuid.uuid4)
    except Exception:  # noqa: BLE001 - includes RunIdentityError for diagnostic-attempt IDs
        return _failure("fatal_internal_failure", run_id)
    attempts = verification["diagnostic_attempts"]

    try:
        validation = validate_errors(verification["processing_errors"], run_id)
    except Exception:  # noqa: BLE001
        return _failure("fatal_internal_failure", run_id, attempts=attempts)
    if not validation["valid"] or not validation["evaluation_permitted"]:
        return _failure("invalid_processing_error_record", run_id, validation["diagnostics"], attempts)

    if verification["application_integrity_failure"]:
        return _failure("application_integrity_failure", run_id, attempts=attempts)

    candidate_facts = verification["candidate_facts"]
    errors = verification["processing_errors"]
    try:
        rule_results = evaluate(candidate_facts)
        rule_id, = rule_results            # extraction mode evaluates exactly one rule
        snapshot = create_initial_snapshot(run_id, candidate_facts, verification["verified_evidence"],
                                           rule_id, rule_results[rule_id], errors)
    except Exception:  # noqa: BLE001
        return _failure("fatal_internal_failure", run_id, attempts=attempts)

    outcome = rule_results[rule_id]["outcome"]
    response = copy.deepcopy({
        "processing_run_id": run_id,
        "application_id": application["application_id"],
        "processing_status": PROCESSING_COMPLETED,
        "failure_code": None,
        # A clean pass only means this automated check needs no review; it is never an approval.
        "review_required": outcome != "pass" or bool(errors),
        "candidate_facts": candidate_facts,
        "rule_results": rule_results,
        "processing_errors": errors,
        "not_found_reasons": verification["not_found_reasons"],
        "verified_evidence": verification["verified_evidence"],
    })
    return ExtractionRun(response, snapshot, attempts)


def process_extracted_application(application, uuid_factory=None, attempt_id_factory=None):
    """Run extraction-mode processing for one document-intake record.

    application is a record from document_intake.intake_application(). The
    authoritative processing_run_id is always created here; any run ID found
    in the application is ignored. uuid_factory and attempt_id_factory are
    injectable for deterministic tests.

    Returns an ExtractionRun whose response is either the success response
    (processing_run_id, application_id, processing_status "completed",
    failure_code None, review_required, candidate_facts, rule_results with
    only "R03", processing_errors, not_found_reasons, verified_evidence) or a
    controlled failure response (processing_run_id, failure_code,
    rule_results None, diagnostics).
    """
    return _process(application, uuid_factory, attempt_id_factory)
