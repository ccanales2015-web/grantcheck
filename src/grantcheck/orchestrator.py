"""Minimal offline orchestration of one GrantCheck application review.

Runs the existing stages in order: document intake, candidate-fact replay
loading, and rule evaluation. It only combines their results into one summary
and decides whether a human reviewer needs to look at the application.

It never approves, rejects, or judges the eligibility of a grant, and it makes
no AI or network calls, writes nothing, and changes no existing module.

Caller mistakes (an invalid application_id, unsupported path types) propagate
as ValueError/TypeError. Only document, replay, and candidate-fact problems
become structured "exception" results.
"""

from grantcheck.document_intake import INTAKE_EXCEPTION, PROCESSING_EXCEPTION, intake_application
from grantcheck.fact_validation import CandidateFactValidationError
from grantcheck.replay_loader import ReplayValidationError, load_replay_facts
from grantcheck.rule_engine import evaluate_rules

PROCESSING_COMPLETED = "completed"

STAGE_INTAKE = "intake"
STAGE_REPLAY = "replay"
STAGE_RULE_EVALUATION = "rule_evaluation"

# Fixed public messages: validation details can quote replay content or fact
# values, so they are not exposed in results.
REPLAY_ERROR_MESSAGE = "Replay data could not be validated."
CANDIDATE_FACT_ERROR_MESSAGE = "Candidate facts could not be validated."


def _result(application_id, processing_status, review_required, candidate_facts, rule_results, errors):
    finding_count = 0
    if rule_results is not None:
        finding_count = sum(result["outcome"] == "finding" for result in rule_results.values())
    return {
        "application_id": application_id,
        "processing_status": processing_status,
        "review_required": review_required,
        "candidate_facts": candidate_facts,
        "rule_results": rule_results,
        "finding_count": finding_count,
        "errors": errors,
    }


def _exception(application_id, errors, candidate_facts=None, rule_results=None):
    return _result(application_id, PROCESSING_EXCEPTION, True, candidate_facts, rule_results, errors)


def process_application(application_id, budget_path, justification_path, narrative_path, replay_path):
    """Run intake, replay loading, and rule evaluation for one application.

    Returns a plain dict with exactly: application_id, processing_status
    ("completed" or "exception"), review_required, candidate_facts,
    rule_results, finding_count (rule outcomes equal to "finding"), and
    errors (a list of structured dicts; empty when nothing went wrong).
    """
    application = intake_application(application_id, budget_path, justification_path, narrative_path)
    if application["intake_status"] == INTAKE_EXCEPTION:
        # Intake errors already identify the document and reason, never its text.
        errors = [
            {"stage": STAGE_INTAKE, "reason": error["reason"], "document": error["document"]}
            for error in application["intake_errors"]
        ]
        return _exception(application_id, errors)

    try:
        candidate_facts = load_replay_facts(application, replay_path)
    except ReplayValidationError:
        return _exception(application_id, [
            {"stage": STAGE_REPLAY, "reason": "invalid_replay", "message": REPLAY_ERROR_MESSAGE},
        ])

    try:
        rule_results = evaluate_rules(candidate_facts)
    except CandidateFactValidationError:
        return _exception(application_id, [
            {"stage": STAGE_RULE_EVALUATION, "reason": "invalid_candidate_facts",
             "message": CANDIDATE_FACT_ERROR_MESSAGE},
        ], candidate_facts=candidate_facts)

    outcomes = {rule_id: result["outcome"] for rule_id, result in rule_results.items()}
    not_evaluated = [rule_id for rule_id, outcome in outcomes.items() if outcome == "not_evaluated"]
    if not_evaluated:
        return _exception(application_id, [
            {"stage": STAGE_RULE_EVALUATION, "reason": "rules_not_evaluated", "rules": not_evaluated},
        ], candidate_facts=candidate_facts, rule_results=rule_results)

    review_required = any(outcome != "pass" for outcome in outcomes.values())
    return _result(application_id, PROCESSING_COMPLETED, review_required, candidate_facts, rule_results, [])
