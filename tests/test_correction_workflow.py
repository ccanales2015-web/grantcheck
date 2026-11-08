"""Stage 6B.2–6B.4 tests: snapshot, evidence, creation, and lifecycle."""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from grantcheck.correction_proposals import (
    CorrectionEvidenceError,
    CorrectionLifecycleEvent,
    CorrectionProposal,
    CorrectionProposalLedger,
    ProposalWorkflowError,
    SnapshotVerificationError,
    StaleCorrectionProposalError,
    create_correction_proposal,
    verify_correction_evidence,
    verify_correction_proposal,
    verify_correction_target,
)
from grantcheck.evaluation_snapshot import create_initial_snapshot
from grantcheck.fact_schema import FACT_SOURCES
from grantcheck.review_authority import (
    ReviewerAuthorizationError,
    ReviewerValidationError,
)


RUN = "12345678-1234-4234-8234-123456789abc"
EXCERPT = "The project duration is 24 months."
DOCUMENT_TEXT = f"Grant budget\n{EXCERPT}\nAdditional detail.\n"


def make_facts(status="present", value=24):
    facts = {
        category: {
            role: {
                "value": None,
                "status": "not_found",
                "evidence_text": None,
            }
            for role in roles
        }
        for category, roles in FACT_SOURCES.items()
    }
    facts["project_duration_months"]["budget"] = {
        "value": value if status == "present" else None,
        "status": status,
        "evidence_text": EXCERPT if status == "present" else None,
    }
    return facts


def make_snapshot(status="present", value=24, sequence=0, run_id=RUN):
    facts = make_facts(status, value)
    return create_initial_snapshot(
        run_id,
        facts,
        {"budget": [], "budget_justification": [], "project_narrative": []},
        "R03",
        {"outcome": "finding", "message": "Review required"},
        [],
    ) if sequence == 0 else replace(
        create_initial_snapshot(
            run_id,
            facts,
            {"budget": [], "budget_justification": [], "project_narrative": []},
            "R03",
            {"outcome": "finding", "message": "Review required"},
            [],
        ),
        sequence=sequence,
    )


def make_application(text=DOCUMENT_TEXT):
    documents = {}
    for role in ("budget", "budget_justification", "project_narrative"):
        role_text = text if role == "budget" else f"{role} document.\n"
        documents[role] = {
            "filename": f"{role}.txt",
            "text": role_text,
            "sha256": hashlib.sha256(role_text.encode("utf-8")).hexdigest(),
        }
    return {
        "application_id": "GC-TEST",
        "documents": documents,
        "intake_errors": [],
        "intake_status": "completed",
        "processing_status": "pending",
    }


def make_proposal(
    *,
    snapshot_sequence=0,
    operation="replace",
    original_value=24,
    proposed_value=36,
    source_type="document_evidence",
    source_details=None,
    run_id=RUN,
):
    if source_details is None:
        start = DOCUMENT_TEXT.index(EXCERPT)
        source_details = {
            "document_role": "budget",
            "document_sha256": hashlib.sha256(DOCUMENT_TEXT.encode()).hexdigest(),
            "evidence_text": EXCERPT,
            "start_offset": start,
            "end_offset": start + len(EXCERPT),
        } if source_type == "document_evidence" else {
            "assertion_basis": "Reviewer supplied a corrected project timeline."
        }
    if operation == "add":
        original_value = None
    if operation == "invalidate":
        proposed_value = None
    return {
        "proposal_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc),
        "reviewer_id": "reviewer_001",
        "processing_run_id": run_id,
        "snapshot_sequence": snapshot_sequence,
        "fact_category": "project_duration_months",
        "document_role": "budget",
        "operation": operation,
        "original_value": original_value,
        "proposed_value": proposed_value,
        "reason": "The submitted value requires correction.",
        "source_type": source_type,
        "source_details": source_details,
    }


def staff():
    return {"reviewer_id": "reviewer_001", "roles": ("staff_reviewer",)}


def other_staff():
    return {"reviewer_id": "reviewer_002", "roles": ("staff_reviewer",)}


def authorized():
    return {"reviewer_id": "reviewer_003", "roles": ("authorized_reviewer",)}


# Snapshot identity, target slot, and original-value verification

def test_valid_target_matches_latest_snapshot():
    proposal = make_proposal()
    assert verify_correction_target(proposal, [make_snapshot()]) is None


def test_target_original_value_must_match_snapshot():
    proposal = make_proposal(original_value=23)
    with pytest.raises(SnapshotVerificationError, match="original_value"):
        verify_correction_target(proposal, [make_snapshot()])


def test_unknown_processing_run_is_rejected():
    proposal = make_proposal(run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    with pytest.raises(SnapshotVerificationError, match="no evaluation snapshots"):
        verify_correction_target(proposal, [make_snapshot()])


def test_nonexistent_future_snapshot_sequence_is_rejected():
    proposal = make_proposal(snapshot_sequence=5)
    with pytest.raises(SnapshotVerificationError, match="nonexistent future"):
        verify_correction_target(proposal, [make_snapshot()])


def test_stale_snapshot_requires_reexamination():
    proposal = make_proposal(snapshot_sequence=0)
    snapshots = [make_snapshot(sequence=0), make_snapshot(sequence=1)]
    with pytest.raises(StaleCorrectionProposalError, match="reexamination"):
        verify_correction_target(proposal, snapshots)


def test_duplicate_snapshot_sequences_are_rejected():
    proposal = make_proposal()
    with pytest.raises(SnapshotVerificationError, match="duplicate snapshot sequence"):
        verify_correction_target(proposal, [make_snapshot(), make_snapshot()])


@pytest.mark.parametrize("status", [
    "missing", "not_found", "uncertain", "unprocessable",
])
def test_add_is_allowed_for_nonpresent_fact_statuses(status):
    proposal = make_proposal(operation="add")
    assert verify_correction_target(proposal, [make_snapshot(status=status)]) is None


def test_add_is_rejected_for_present_fact():
    proposal = make_proposal(operation="add")
    with pytest.raises(SnapshotVerificationError, match="non-present"):
        verify_correction_target(proposal, [make_snapshot(status="present")])


def test_replace_is_rejected_for_nonpresent_fact():
    proposal = make_proposal()
    with pytest.raises(SnapshotVerificationError, match="requires a present"):
        verify_correction_target(proposal, [make_snapshot(status="missing")])


def test_invalidate_is_rejected_for_nonpresent_fact():
    proposal = make_proposal(operation="invalidate")
    with pytest.raises(SnapshotVerificationError, match="requires a present"):
        verify_correction_target(proposal, [make_snapshot(status="not_found")])


def test_invalidation_preserves_original_value_in_proposal():
    proposal = make_proposal(operation="invalidate")
    assert proposal["original_value"] == 24
    assert proposal["proposed_value"] is None
    assert verify_correction_target(proposal, [make_snapshot()]) is None


# Evidence verification

def test_document_evidence_matches_intake_hash_and_exact_offsets():
    proposal = make_proposal()
    assert verify_correction_evidence(proposal, make_application()) is None


def test_document_evidence_rejects_tampered_document_hash():
    proposal = make_proposal()
    proposal["source_details"]["document_sha256"] = "b" * 64
    with pytest.raises(CorrectionEvidenceError, match="SHA-256"):
        verify_correction_evidence(proposal, make_application())


def test_document_evidence_rejects_tampered_intake_text_hash():
    application = make_application()
    application["documents"]["budget"]["text"] += "tampered"
    with pytest.raises(CorrectionEvidenceError, match="SHA-256"):
        verify_correction_evidence(make_proposal(), application)


def test_document_evidence_rejects_wrong_excerpt_offsets():
    proposal = make_proposal()
    proposal["source_details"]["start_offset"] += 1
    proposal["source_details"]["end_offset"] += 1
    with pytest.raises(CorrectionEvidenceError, match="exact document text"):
        verify_correction_evidence(proposal, make_application())


def test_document_evidence_requires_completed_intake():
    application = make_application()
    application["intake_status"] = "exception"
    with pytest.raises(CorrectionEvidenceError, match="intake must be completed"):
        verify_correction_evidence(make_proposal(), application)


def test_human_assertion_remains_distinct_from_document_evidence():
    proposal = make_proposal(source_type="human_assertion")
    assert verify_correction_evidence(proposal, {}) is None
    assert proposal["source_type"] == "human_assertion"
    assert set(proposal["source_details"]) == {"assertion_basis"}


def test_combined_verification_checks_snapshot_and_evidence():
    proposal = make_proposal()
    assert verify_correction_proposal(
        proposal, [make_snapshot()], make_application()
    ) is None


# Immutable creation

def test_creation_uses_latest_snapshot_and_derives_original_value():
    proposal = create_correction_proposal(
        staff(),
        RUN,
        [make_snapshot(sequence=0), make_snapshot(sequence=1, value=30)],
        make_application(),
        fact_category="project_duration_months",
        document_role="budget",
        operation="replace",
        proposed_value=36,
        reason="A revised budget corrects the duration.",
        source_type="document_evidence",
        source_details=make_proposal()["source_details"],
    )
    assert isinstance(proposal, CorrectionProposal)
    assert proposal["snapshot_sequence"] == 1
    assert proposal["original_value"] == 30
    assert proposal["proposed_value"] == 36
    assert proposal["reviewer_id"] == "reviewer_001"


def test_created_proposal_and_nested_source_details_are_immutable():
    proposal = create_correction_proposal(
        staff(), RUN, [make_snapshot()], make_application(),
        fact_category="project_duration_months",
        document_role="budget",
        operation="replace",
        proposed_value=36,
        reason="Correct the duration.",
        source_type="document_evidence",
        source_details=make_proposal()["source_details"],
    )
    with pytest.raises((AttributeError, TypeError)):
        proposal.proposed_value = 48
    with pytest.raises(TypeError):
        proposal.source_details["document_role"] = "project_narrative"
    exported = proposal.to_dict()
    exported["source_details"]["document_role"] = "project_narrative"
    assert proposal.source_details["document_role"] == "budget"


def test_creation_requires_reviewer_submit_permission():
    with pytest.raises(ReviewerValidationError):
        create_correction_proposal(
            {"reviewer_id": "bad", "roles": ("unknown",)},
            RUN, [make_snapshot()], make_application(),
            fact_category="project_duration_months",
            document_role="budget",
            operation="replace",
            proposed_value=36,
            reason="Correct the duration.",
            source_type="human_assertion",
            source_details={"assertion_basis": "Reviewer supplied a revised value."},
        )


def test_creation_accepts_human_assertion_without_fabricating_document_source():
    proposal = create_correction_proposal(
        staff(), RUN, [make_snapshot()], {},
        fact_category="project_duration_months",
        document_role="budget",
        operation="replace",
        proposed_value=36,
        reason="Correct the duration.",
        source_type="human_assertion",
        source_details={"assertion_basis": "Reviewer supplied a revised value."},
    )
    assert proposal.source_type == "human_assertion"
    assert set(proposal.source_details) == {"assertion_basis"}


# Append-only proposal lifecycle and overlap flags

def test_ledger_submission_records_separate_lifecycle_event():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    assert ledger.get_status(proposal.proposal_id) == "pending"
    assert ledger.events[0].event_type == "proposal_submitted"
    assert isinstance(ledger.events[0], CorrectionLifecycleEvent)
    assert len(proposal) == 13


def test_overlapping_pending_proposals_are_flagged_not_merged_or_rejected():
    ledger = CorrectionProposalLedger()
    first = ledger.submit(make_proposal())
    second = ledger.submit(make_proposal())
    assert ledger.get_status(first.proposal_id) == "pending"
    assert ledger.get_status(second.proposal_id) == "pending"
    assert ledger.overlap_flags(first.proposal_id)
    assert ledger.overlap_flags(second.proposal_id)
    assert ledger.get_proposal(first.proposal_id).proposal_id == first.proposal_id


def test_different_fact_slots_do_not_overlap():
    ledger = CorrectionProposalLedger()
    first = make_proposal()
    second = make_proposal()
    second["fact_category"] = "personnel_cost_usd"
    second["original_value"] = 50000
    second["proposed_value"] = 60000
    second["document_role"] = "budget"
    ledger.submit(first)
    submitted = ledger.submit(second)
    assert ledger.overlap_flags(submitted.proposal_id) == ()


def test_only_proposing_reviewer_can_withdraw():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    with pytest.raises(ReviewerAuthorizationError, match="proposing reviewer"):
        ledger.withdraw(proposal.proposal_id, other_staff())
    event = ledger.withdraw(proposal.proposal_id, staff())
    assert event.event_type == "proposal_withdrawn"
    assert ledger.get_status(proposal.proposal_id) == "withdrawn"


def test_withdrawal_does_not_mutate_or_delete_original_proposal():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    before = proposal.to_dict()
    ledger.withdraw(proposal.proposal_id, staff())
    assert ledger.get_proposal(proposal.proposal_id).to_dict() == before
    assert len(ledger.proposals) == 1


def test_withdrawn_proposal_cannot_be_decided():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    ledger.withdraw(proposal.proposal_id, staff())
    with pytest.raises(ProposalWorkflowError, match="pending"):
        ledger.decide(proposal.proposal_id, authorized(), "accepted")


def test_only_authorized_reviewer_can_decide():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    with pytest.raises(ReviewerAuthorizationError):
        ledger.decide(proposal.proposal_id, staff(), "accepted")


def test_authorized_decision_is_a_separate_event_not_a_proposal_mutation():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    before = proposal.to_dict()
    event = ledger.decide(proposal.proposal_id, authorized(), "accepted")
    assert event.event_type == "correction_decided"
    assert event.details["decision"] == "accepted"
    assert ledger.get_status(proposal.proposal_id) == "accepted"
    assert ledger.get_proposal(proposal.proposal_id).to_dict() == before


def test_only_accepted_correction_can_request_reevaluation():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    with pytest.raises(ProposalWorkflowError, match="accepted"):
        ledger.request_reevaluation(proposal.proposal_id, authorized())
    ledger.decide(proposal.proposal_id, authorized(), "accepted")
    event = ledger.request_reevaluation(proposal.proposal_id, authorized())
    assert event.event_type == "reevaluation_requested"


def test_reevaluation_request_is_not_a_claim_that_reevaluation_ran():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    ledger.decide(proposal.proposal_id, authorized(), "accepted")
    event = ledger.request_reevaluation(proposal.proposal_id, authorized())
    assert event.event_type == "reevaluation_requested"
    assert ledger.get_status(proposal.proposal_id) == "accepted"


def test_proposal_id_cannot_be_resubmitted_after_withdrawal():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    ledger.withdraw(proposal.proposal_id, staff())
    with pytest.raises(ProposalWorkflowError, match="already been submitted"):
        ledger.submit(proposal)


def test_lifecycle_events_are_immutable():
    ledger = CorrectionProposalLedger()
    proposal = ledger.submit(make_proposal())
    event = ledger.events[0]
    with pytest.raises((AttributeError, TypeError)):
        event.event_type = "changed"
    with pytest.raises(TypeError):
        event.details["changed"] = True
    assert ledger.get_proposal(proposal.proposal_id) is proposal
