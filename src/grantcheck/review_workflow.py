"""Stage 9: explicit, offline review-session integration.

The session ties the existing intake, evaluation, correction and persistence
interfaces together. It does not authenticate users, decide funding, or make
AI/network calls. Source documents must be supplied again after a restart to
verify document-backed corrections; they are deliberately not stored in SQLite.
"""

from dataclasses import dataclass
from uuid import uuid4

from grantcheck.correction_proposals import (
    create_correction_proposal, verify_correction_evidence,
)
from grantcheck.correction_reevaluation import reevaluate_accepted_correction
from grantcheck.document_intake import intake_application
from grantcheck.evaluation_snapshot import create_initial_snapshot
from grantcheck.immutable import deep_thaw
from grantcheck.orchestrator import process_application
from grantcheck.review_authority import check_review_permission
from grantcheck.rule_engine import evaluate_rules
from grantcheck.sqlite_persistence import SQLiteReviewStore


class ReviewWorkflowError(ValueError):
    """A review session cannot safely proceed."""


def _outcome_needs_review(outcome):
    return outcome != 'pass'


def build_review_report(snapshot, ledger):
    """A detached, human-readable data report; never a funding decision."""
    facts = deep_thaw(snapshot.candidate_facts)
    evidence = deep_thaw(snapshot.verified_evidence)
    rules = evaluate_rules(facts)
    proposal_rows = []
    for proposal in ledger.proposals:
        if proposal.processing_run_id != snapshot.processing_run_id:
            continue
        proposal_rows.append({
            'proposal_id': proposal.proposal_id,
            'category': proposal.fact_category,
            'document_role': proposal.document_role,
            'operation': proposal.operation,
            'source_type': proposal.source_type,
            'status': ledger.get_status(proposal.proposal_id),
            'overlap_flagged': bool(ledger.overlap_flags(proposal.proposal_id)),
        })
    return {
        'processing_run_id': snapshot.processing_run_id,
        'snapshot_sequence': snapshot.sequence,
        'rule_results': rules,
        'review_required': any(_outcome_needs_review(r['outcome']) for r in rules.values()),
        'candidate_facts': facts,
        'correction_history': evidence.get('correction_history', []),
        'proposals': proposal_rows,
        'processing_errors': deep_thaw(snapshot.processing_errors),
        'funding_decision': None,
    }


@dataclass
class ReviewSession:
    """An explicit session with caller-supplied original intake documents."""
    store: SQLiteReviewStore
    application: dict
    processing_run_id: str

    @property
    def snapshots(self):
        return self.store.load_snapshots(self.processing_run_id)

    def report(self):
        history = self.snapshots
        if not history:
            raise ReviewWorkflowError('processing run has no snapshots')
        return build_review_report(history[-1], self.store.load_ledger())

    def submit(self, reviewer, **correction):
        ledger = self.store.load_ledger()
        proposal = create_correction_proposal(
            reviewer, self.processing_run_id, self.snapshots, self.application,
            **correction,
        )
        ledger.submit(proposal)
        self.store.save_ledger(ledger)
        return proposal

    def decide(self, proposal_id, reviewer, decision):
        ledger = self.store.load_ledger()
        proposal = ledger.get_proposal(proposal_id)
        if proposal.processing_run_id != self.processing_run_id:
            raise ReviewWorkflowError('proposal belongs to a different processing run')
        event = ledger.decide(proposal_id, reviewer, decision)
        self.store.save_ledger(ledger)
        return event

    def withdraw(self, proposal_id, reviewer):
        ledger = self.store.load_ledger()
        proposal = ledger.get_proposal(proposal_id)
        if proposal.processing_run_id != self.processing_run_id:
            raise ReviewWorkflowError('proposal belongs to a different processing run')
        event = ledger.withdraw(proposal_id, reviewer)
        self.store.save_ledger(ledger)
        return event

    def reevaluate(self, proposal_id, reviewer):
        check_review_permission(reviewer, 'trigger_reevaluation')
        ledger = self.store.load_ledger()
        proposal = ledger.get_proposal(proposal_id)
        if proposal.processing_run_id != self.processing_run_id:
            raise ReviewWorkflowError('proposal belongs to a different processing run')
        result = reevaluate_accepted_correction(
            ledger, proposal_id, reviewer, self.snapshots, self.application,
        )
        # Snapshot is saved first; if ledger persistence fails, caller can
        # recover by inspecting the snapshot and reconciling its audit trail.
        self.store.append_snapshot(result.snapshot)
        self.store.save_ledger(ledger)
        return result


def start_review(store, application_id, budget_path, justification_path,
                 narrative_path, replay_path, *, processing_run_id=None):
    """Process a replay-based application and persist its initial R03 snapshot.

    Existing orchestration evaluates all four rules; Stage 7's snapshot
    contract supports a single rule_id per run, so only R03 is persisted as
    the correction baseline. All four rules are recomputed for reports.
    """
    if not isinstance(store, SQLiteReviewStore):
        raise TypeError('store must be a SQLiteReviewStore')
    result = process_application(application_id, budget_path, justification_path,
                                 narrative_path, replay_path)
    if result['processing_status'] != 'completed':
        raise ReviewWorkflowError('application processing did not complete; inspect process_application result')
    application = intake_application(application_id, budget_path,
                                     justification_path, narrative_path)
    run_id = processing_run_id or str(uuid4())
    snapshot = create_initial_snapshot(
        run_id, result['candidate_facts'], result['provenance'], 'R03',
        result['rule_results']['R03'], result['errors'],
    )
    store.append_snapshot(snapshot)
    return ReviewSession(store, application, run_id)


def resume_review(store, processing_run_id, application):
    """Resume after restart with original intake documents for hash checks."""
    if not isinstance(store, SQLiteReviewStore):
        raise TypeError('store must be a SQLiteReviewStore')
    if not store.load_snapshots(processing_run_id):
        raise ReviewWorkflowError('processing run does not exist')
    if not isinstance(application, dict) or application.get('intake_status') != 'completed':
        raise ReviewWorkflowError('completed original intake is required')
    return ReviewSession(store, application, processing_run_id)
