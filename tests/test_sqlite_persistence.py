"""Stage 8 persistence tests: restart, append-only, atomicity, and round trips."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from grantcheck.sqlite_persistence import SQLiteReviewStore, PersistenceError
from grantcheck.correction_proposals import CorrectionProposalLedger, CorrectionLifecycleEvent
from grantcheck.evaluation_snapshot import EvaluationSnapshot
from grantcheck.immutable import deep_freeze


def snapshot(run=None, sequence=0):
    return EvaluationSnapshot(
        processing_run_id=run or str(uuid4()), sequence=sequence,
        trigger='initial_extraction_evaluation', rule_id='R03',
        candidate_facts=deep_freeze({'x': [1, 2]}),
        verified_evidence=deep_freeze({}), rule_result=deep_freeze({'status': 'pending'}),
        processing_errors=deep_freeze([]),
    )


def test_snapshot_restart_and_detachment(tmp_path):
    path = tmp_path / 'review.db'
    original = snapshot()
    with SQLiteReviewStore(path) as store:
        assert store.append_snapshot(original) is True
        assert store.append_snapshot(original) is False
    with SQLiteReviewStore(path) as store:
        restored, = store.load_snapshots(original.processing_run_id)
        assert restored.to_dict() == original.to_dict()
        with pytest.raises(TypeError):
            restored.candidate_facts['x'] = 4
        assert store.load_snapshots('00000000-0000-0000-0000-000000000000') == ()


def test_snapshot_conflict_and_sequence(tmp_path):
    with SQLiteReviewStore(tmp_path / 'a.db') as store:
        first = snapshot()
        store.append_snapshot(first)
        with pytest.raises(PersistenceError):
            store.append_snapshot(EvaluationSnapshot(**{**first.to_dict(), "trigger": "different"}))
        store.append_snapshot(snapshot(first.processing_run_id, 1))
        assert [x.sequence for x in store.load_snapshots(first.processing_run_id)] == [0, 1]


def test_snapshot_invalid_values(tmp_path):
    with SQLiteReviewStore(tmp_path / 'a.db') as store:
        with pytest.raises(PersistenceError):
            store.append_snapshot({'processing_run_id': 'wrong', 'sequence': 0})
        with pytest.raises(PersistenceError):
            store.append_snapshot({**snapshot().to_dict(), 'sequence': True})
        with pytest.raises(PersistenceError):
            store.append_snapshot({**snapshot().to_dict(), 'candidate_facts': {'bad': float('nan')}})


def test_event_requires_existing_proposal(tmp_path):
    event = CorrectionLifecycleEvent(str(uuid4()), str(uuid4()), 'proposal_withdrawn', 'staff', datetime.now(timezone.utc), {})
    with SQLiteReviewStore(tmp_path / 'a.db') as store:
        with pytest.raises(PersistenceError):
            store.append_event(event)
        assert store.load_events() == ()


def test_empty_ledger_restart(tmp_path):
    path = tmp_path / 'a.db'
    with SQLiteReviewStore(path) as store:
        store.save_ledger(CorrectionProposalLedger())
    with SQLiteReviewStore(path) as store:
        assert store.load_ledger().proposals == ()
        assert store.load_ledger().events == ()


def test_database_triggers_prevent_updates_and_deletes(tmp_path):
    import sqlite3
    with SQLiteReviewStore(tmp_path / 'a.db') as store:
        item = snapshot()
        store.append_snapshot(item)
        with pytest.raises(sqlite3.IntegrityError):
            with store._db:
                store._db.execute('DELETE FROM snapshots')
        with pytest.raises(sqlite3.IntegrityError):
            with store._db:
                store._db.execute('UPDATE snapshots SET payload=?', ('{}',))
        assert len(store.load_snapshots()) == 1


def valid_proposal():
    from grantcheck.correction_proposals import CorrectionProposal
    return CorrectionProposal(
        proposal_id=str(uuid4()), created_at=datetime.now(timezone.utc),
        reviewer_id='staff', processing_run_id=str(uuid4()), snapshot_sequence=0,
        fact_category='project_duration_months', document_role='project_narrative',
        operation='replace', original_value=12, proposed_value=18,
        reason='Correct the duration', source_type='human_assertion',
        source_details={'assertion_basis': 'Reviewer supplied correction'},
    )


def test_proposal_event_and_ledger_restart(tmp_path):
    path = tmp_path / 'review.db'
    proposal = valid_proposal()
    event = CorrectionLifecycleEvent(str(uuid4()), proposal.proposal_id, 'correction_decided',
                                     'authorized', datetime.now(timezone.utc), {'decision': 'accepted'})
    with SQLiteReviewStore(path) as store:
        assert store.append_proposal(proposal)
        assert not store.append_proposal(proposal)
        assert store.append_event(event)
        assert not store.append_event(event)
    with SQLiteReviewStore(path) as store:
        ledger = store.load_ledger()
        assert ledger.get_status(proposal.proposal_id) == 'accepted'
        assert ledger.get_proposal(proposal.proposal_id).to_dict() == proposal.to_dict()
        assert ledger.events[0].to_dict() == event.to_dict()
        store.save_ledger(ledger)
        assert len(store.load_events()) == 1
        with pytest.raises(PersistenceError):
            store.append_proposal(type(proposal)(**{**proposal.to_dict(), 'reason': 'different'}))


def test_save_ledger_is_atomic(tmp_path):
    path = tmp_path / 'review.db'
    ledger = CorrectionProposalLedger()
    proposal = valid_proposal()
    ledger._proposals[proposal.proposal_id] = proposal
    ledger._events.append(CorrectionLifecycleEvent(
        str(uuid4()), str(uuid4()), 'proposal_withdrawn', 'staff', datetime.now(timezone.utc), {}))
    with SQLiteReviewStore(path) as store:
        with pytest.raises(PersistenceError):
            store.save_ledger(ledger)
        assert store.load_proposals() == ()
