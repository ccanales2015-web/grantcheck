"""Stage 7 correction application and immutable reevaluation tests."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
import hashlib

import pytest

from grantcheck.correction_proposals import (
    CorrectionProposalLedger, create_correction_proposal,
    StaleCorrectionProposalError,
)
from grantcheck.correction_reevaluation import (
    reevaluate_accepted_correction, CorrectionReevaluationError,
)
from grantcheck.evaluation_snapshot import create_initial_snapshot
from grantcheck.fact_schema import FACT_SOURCES
from grantcheck.rule_engine import evaluate_duration_rule


REVIEWER = {"reviewer_id": "reviewer1", "roles": ["authorized_reviewer"]}


def setup_case(operation="replace", source_type="human_assertion", status="present"):
    facts = {}
    for category, roles in FACT_SOURCES.items():
        value = {"project_duration_months": 12, "personnel_role": "Engineer",
                 "personnel_cost_usd": 100, "personnel_effort_percent": 50,
                 "travel_cost_usd": 20, "travel_justification": "adequate"}[category]
        facts[category] = {role: {"value": value, "status": "present", "evidence_text": "Original statement"}
                           for role in roles}
    target = facts["project_duration_months"]["budget"]
    if status != "present":
        target.update(value=None, status=status, evidence_text="Uncertain extraction" if status == "uncertain" else None)
    run_id = str(uuid4())
    snapshot = create_initial_snapshot(run_id, facts, {}, "R03",
                                       evaluate_duration_rule(facts)["R03"], [])
    text = "The project duration is 24 months."
    application = {"intake_status": "completed", "documents": {
        "budget": {"filename": "budget.txt", "text": text,
                   "sha256": hashlib.sha256(text.encode()).hexdigest()}}}
    details = ({"assertion_basis": "Reviewer supplied an updated duration"}
               if source_type == "human_assertion" else {
                   "document_role": "budget", "document_sha256": application["documents"]["budget"]["sha256"],
                   "evidence_text": text, "start_offset": 0, "end_offset": len(text)})
    proposal = create_correction_proposal(
        REVIEWER, run_id, [snapshot], application,
        fact_category="project_duration_months", document_role="budget",
        operation=operation, proposed_value=None if operation == "invalidate" else 24,
        reason="Reviewer correction", source_type=source_type, source_details=details)
    ledger = CorrectionProposalLedger()
    ledger.submit(proposal)
    ledger.decide(proposal.proposal_id, REVIEWER, "accepted")
    return ledger, proposal, snapshot, application


@pytest.mark.parametrize("operation,status,expected_status,expected_value", [
    ("replace", "present", "present", 24),
    ("add", "missing", "present", 24),
    ("add", "not_found", "present", 24),
    ("add", "uncertain", "present", 24),
    ("invalidate", "present", "unprocessable", None),
])
def test_reevaluation_operations(operation, status, expected_status, expected_value):
    ledger, proposal, previous, app = setup_case(operation, status=status)
    original = previous.to_dict()
    result = reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous], app)
    assert result.snapshot.sequence == 1
    assert result.snapshot.trigger == "accepted_correction_reevaluation"
    record = result.snapshot.to_dict()["candidate_facts"]["project_duration_months"]["budget"]
    assert record["status"] == expected_status
    assert record["value"] == expected_value
    assert previous.to_dict() == original
    assert result.event.event_type == "reevaluation_requested"
    audit = result.snapshot.to_dict()["verified_evidence"]["correction_history"][0]
    assert audit["source_type"] == "human_assertion"
    assert audit["document_verified"] is False
    assert audit["invalidated"] == (operation == "invalidate")


def test_document_evidence_is_verified_and_labeled():
    ledger, proposal, previous, app = setup_case(source_type="document_evidence")
    result = reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous], app)
    audit = result.snapshot.to_dict()["verified_evidence"]["correction_history"][0]
    assert audit["document_verified"] is True
    assert audit["source_type"] == "document_evidence"


def test_stale_proposal_does_not_record_event():
    ledger, proposal, previous, app = setup_case()
    newer = previous.to_dict()
    newer["sequence"] = 1
    before = len(ledger.events)
    with pytest.raises(StaleCorrectionProposalError):
        reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous, newer], app)
    assert len(ledger.events) == before


def test_repeat_reevaluation_is_blocked():
    ledger, proposal, previous, app = setup_case()
    reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous], app)
    with pytest.raises(CorrectionReevaluationError):
        reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous], app)


def test_tampered_evidence_fails_without_event():
    ledger, proposal, previous, app = setup_case(source_type="document_evidence")
    app["documents"]["budget"]["text"] = "tampered"
    before = len(ledger.events)
    with pytest.raises(ValueError):
        reevaluate_accepted_correction(ledger, proposal.proposal_id, REVIEWER, [previous], app)
    assert len(ledger.events) == before


def test_non_authorized_reviewer_is_rejected():
    ledger, proposal, previous, app = setup_case()
    with pytest.raises(PermissionError):
        reevaluate_accepted_correction(ledger, proposal.proposal_id,
                                      {"reviewer_id": "staff", "roles": ["staff_reviewer"]},
                                      [previous], app)
