"""Stage 9 end-to-end offline workflow and reporting tests."""
from pathlib import Path

import pytest

from grantcheck.review_workflow import (
    ReviewWorkflowError, start_review, resume_review,
)
from grantcheck.sqlite_persistence import SQLiteReviewStore
from grantcheck.document_intake import intake_application
from grantcheck.review_authority import ReviewerAuthorizationError

DATA = Path(__file__).resolve().parents[1] / 'sample_data'
STAFF = {'reviewer_id': 'staff-1', 'roles': ['staff_reviewer']}
AUTHORIZED = {'reviewer_id': 'admin-1', 'roles': ['authorized_reviewer']}


def begin(store, scenario='S04_duration_conflict'):
    path = DATA / scenario
    return start_review(store, 'GC-S04', path / 'budget.txt',
                        path / 'budget_justification.txt',
                        path / 'project_narrative.txt', path / 'replay_facts.json')


def correction():
    return dict(fact_category='project_duration_months',
                document_role='budget', operation='replace', proposed_value=36,
                reason='Staff identified a revised project duration.',
                source_type='human_assertion',
                source_details={'assertion_basis': 'Reviewer received revised schedule.'})


def test_initial_processing_and_persistent_report(tmp_path):
    db = tmp_path / 'review.db'
    with SQLiteReviewStore(db) as store:
        session = begin(store)
        run = session.processing_run_id
        report = session.report()
        assert report['snapshot_sequence'] == 0
        assert report['rule_results']['R03']['outcome'] == 'finding'
        assert report['review_required'] is True
        assert report['funding_decision'] is None
        assert len(store.load_snapshots(run)) == 1
    path = DATA / 'S04_duration_conflict'
    application = intake_application('GC-S04', path / 'budget.txt',
                                    path / 'budget_justification.txt', path / 'project_narrative.txt')
    with SQLiteReviewStore(db) as store:
        resumed = resume_review(store, run, application)
        assert resumed.report()['snapshot_sequence'] == 0


def test_proposal_decision_reevaluation_and_recovery(tmp_path):
    db = tmp_path / 'review.db'
    with SQLiteReviewStore(db) as store:
        session = begin(store)
        run = session.processing_run_id
        proposal = session.submit(STAFF, **correction())
        assert session.report()['proposals'][0]['status'] == 'pending'
        with pytest.raises(ReviewerAuthorizationError):
            session.decide(proposal.proposal_id, STAFF, 'accepted')
        session.decide(proposal.proposal_id, AUTHORIZED, 'accepted')
        before = session.report()['candidate_facts']['project_duration_months']['budget']['value']
        outcome = session.reevaluate(proposal.proposal_id, AUTHORIZED)
        assert outcome.snapshot.sequence == 1
        assert session.report()['snapshot_sequence'] == 1
        assert session.report()['candidate_facts']['project_duration_months']['budget']['value'] == 36
        assert session.report()['correction_history'][0]['source_type'] == 'human_assertion'
        assert session.report()['correction_history'][0]['document_verified'] is False
        assert store.load_snapshots(run)[0].to_dict()['candidate_facts']['project_duration_months']['budget']['value'] == before
        with pytest.raises(Exception):
            session.reevaluate(proposal.proposal_id, AUTHORIZED)
        application = session.application
    with SQLiteReviewStore(db) as store:
        session = resume_review(store, run, application)
        assert session.report()['snapshot_sequence'] == 1
        assert session.report()['proposals'][0]['status'] == 'accepted'
        assert len(store.load_ledger().events) == 3


def test_wrong_run_and_failed_processing(tmp_path):
    with SQLiteReviewStore(tmp_path / 'review.db') as store:
        with pytest.raises(ReviewWorkflowError):
            resume_review(store, '12345678-1234-4234-8234-123456789abc',
                          {'intake_status': 'completed'})
        path = DATA / 'S01_consistent'
        with pytest.raises(ReviewWorkflowError):
            start_review(store, 'GC-S04', path / 'missing.txt',
                         path / 'budget_justification.txt',
                         path / 'project_narrative.txt', path / 'replay_facts.json')
        assert store.load_snapshots() == ()
