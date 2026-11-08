"""Stage 7: apply an accepted correction and append an immutable reevaluation.

No source document, prior snapshot, or submitted proposal is modified. This
module does not persist records to disk or make funding decisions.
"""

import copy
from dataclasses import dataclass

from grantcheck.correction_proposals import (
    ProposalWorkflowError, StaleCorrectionProposalError,
    verify_correction_target, verify_correction_evidence,
    latest_snapshot_for_run,
)
from grantcheck.evaluation_snapshot import EvaluationSnapshot
from grantcheck.fact_validation import validate_candidate_facts
from grantcheck.immutable import deep_freeze, deep_thaw
from grantcheck.review_authority import check_review_permission
from grantcheck.rule_engine import evaluate_duration_rule, evaluate_rules
from grantcheck.run_identity import same_run


class CorrectionReevaluationError(ProposalWorkflowError):
    """The accepted correction cannot safely be applied or reevaluated."""


@dataclass(frozen=True)
class ReevaluationResult:
    """A new immutable snapshot and its append-only correction audit event."""

    snapshot: EvaluationSnapshot
    event: object


def _snapshot_data(snapshot, name):
    return snapshot[name] if isinstance(snapshot, dict) else getattr(snapshot, name)


def reevaluate_accepted_correction(
    ledger, proposal_id, reviewer, snapshots, application
):
    """Apply one accepted proposal to a detached copy and reevaluate its rule.

    Caller owns the snapshot history and must append result.snapshot after this
    call. The ledger records a successful reevaluation only after the new
    snapshot has been fully constructed. Subsequent calls for the same proposal
    are blocked. A stale proposal must be reexamined, never silently rebased.

    The source classification and invalidation meaning are preserved in the
    new snapshot's verified_evidence correction_history audit entries. These
    entries are *not* document-verified evidence for human assertions.
    """
    check_review_permission(reviewer, "trigger_reevaluation")
    proposal = ledger.get_proposal(proposal_id)
    if ledger.get_status(proposal_id) != "accepted":
        raise CorrectionReevaluationError("only accepted corrections may be reevaluated")
    if any(event.proposal_id == proposal_id and event.event_type == "reevaluation_requested"
           for event in ledger.events):
        raise CorrectionReevaluationError("this correction has already been reevaluated or requested")

    history = tuple(snapshots)
    try:
        verify_correction_target(proposal, history)
        verify_correction_evidence(proposal, application)
    except StaleCorrectionProposalError:
        raise

    previous = latest_snapshot_for_run(proposal.processing_run_id, history)
    facts = deep_thaw(_snapshot_data(previous, "candidate_facts"))
    evidence = deep_thaw(_snapshot_data(previous, "verified_evidence"))
    errors = deep_thaw(_snapshot_data(previous, "processing_errors"))
    if not isinstance(facts, dict) or not isinstance(evidence, dict):
        raise CorrectionReevaluationError("snapshot contents cannot be reevaluated")
    facts = copy.deepcopy(facts)
    evidence = copy.deepcopy(evidence)
    record = facts[proposal.fact_category][proposal.document_role]

    if proposal.operation in {"replace", "add"}:
        record["value"] = proposal.proposed_value
        record["status"] = "present"
        if proposal.source_type == "document_evidence":
            record["evidence_text"] = proposal.source_details["evidence_text"]
        else:
            # CandidateFact schema requires evidence_text for present values.
            # The audit below explicitly marks this as an unverified assertion.
            record["evidence_text"] = proposal.source_details["assertion_basis"]
    else:
        # Existing schema has no 'invalidated' status. Preserve its meaning in
        # the correction audit and use 'unprocessable' to exclude rule comparison.
        record.update(value=None, status="unprocessable", evidence_text=None)

    validate_candidate_facts(facts)
    rule_id = _snapshot_data(previous, "rule_id")
    if rule_id == "R03":
        result = evaluate_duration_rule(facts)["R03"]
    elif rule_id in {"R01", "R02", "R04"}:
        result = evaluate_rules(facts)[rule_id]
    else:
        raise CorrectionReevaluationError(f"unsupported snapshot rule_id {rule_id!r}")

    audit = {
        "proposal_id": proposal.proposal_id,
        "fact_category": proposal.fact_category,
        "document_role": proposal.document_role,
        "operation": proposal.operation,
        "source_type": proposal.source_type,
        "source_details": deep_thaw(proposal.source_details),
        "reviewer_id": reviewer["reviewer_id"],
        "previous_snapshot_sequence": _snapshot_data(previous, "sequence"),
        "document_verified": proposal.source_type == "document_evidence",
        "invalidated": proposal.operation == "invalidate",
    }
    evidence.setdefault("correction_history", []).append(audit)
    sequence = _snapshot_data(previous, "sequence") + 1
    snapshot = EvaluationSnapshot(
        processing_run_id=proposal.processing_run_id,
        sequence=sequence,
        trigger="accepted_correction_reevaluation",
        rule_id=rule_id,
        candidate_facts=deep_freeze(copy.deepcopy(facts)),
        verified_evidence=deep_freeze(copy.deepcopy(evidence)),
        rule_result=deep_freeze(copy.deepcopy(result)),
        processing_errors=deep_freeze(copy.deepcopy(errors)),
    )
    # Only after all validation and evaluation succeeds do we emit an event.
    event = ledger.request_reevaluation(proposal_id, reviewer)
    return ReevaluationResult(snapshot=snapshot, event=event)
