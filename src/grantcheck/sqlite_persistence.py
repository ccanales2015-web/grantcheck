"""Stage 8: transactional, append-only SQLite storage for GrantCheck review history.

This is an explicit persistence boundary, not automatic persistence of the
in-memory ledger. Call save_ledger after lifecycle changes, and append_snapshot
for each newly returned reevaluation snapshot. Never replace prior snapshots.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from grantcheck.correction_proposals import (
    CorrectionLifecycleEvent, CorrectionProposal, CorrectionProposalLedger,
    validate_correction_proposal,
)
from grantcheck.evaluation_snapshot import EvaluationSnapshot
from grantcheck.immutable import deep_freeze, deep_thaw
from grantcheck.run_identity import parse_run_id


class PersistenceError(ValueError):
    """Invalid data or a conflicting immutable persisted record."""


def _utc_string(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise PersistenceError('timestamp must be a timezone-aware UTC datetime')
    return value.isoformat()


def _decode_utc(value):
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PersistenceError('invalid stored timestamp') from exc
    _utc_string(result)
    return result


def _json(data):
    try:
        return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PersistenceError('record is not valid JSON') from exc


def _unjson(value):
    try:
        return json.loads(value, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (TypeError, ValueError) as exc:
        raise PersistenceError('stored record contains invalid JSON') from exc


class SQLiteReviewStore:
    """Durable append-only review store; close it or use it as a context manager."""

    def __init__(self, path):
        if str(path) == ':memory:':
            self.path = ':memory:'
        else:
            self.path = str(Path(path))
        self._db = sqlite3.connect(self.path)
        self._db.execute('PRAGMA foreign_keys = ON')
        self._db.execute('PRAGMA busy_timeout = 5000')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshots (
                processing_run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence >= 0),
                payload TEXT NOT NULL,
                PRIMARY KEY(processing_run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lifecycle_events (
                event_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
                payload TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
            BEGIN SELECT RAISE(ABORT, 'snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
            BEGIN SELECT RAISE(ABORT, 'snapshots are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS proposals_no_update BEFORE UPDATE ON proposals
            BEGIN SELECT RAISE(ABORT, 'proposals are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS proposals_no_delete BEFORE DELETE ON proposals
            BEGIN SELECT RAISE(ABORT, 'proposals are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON lifecycle_events
            BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
        ''')
        self._db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._db.close()

    def _insert(self, table, key_columns, key_values, payload):
        keys = ' AND '.join(f'{name}=?' for name in key_columns)
        old = self._db.execute(f'SELECT payload FROM {table} WHERE {keys}', key_values).fetchone()
        if old is not None:
            if old[0] == payload:
                return False  # exact replay is idempotent
            raise PersistenceError(f'conflicting immutable {table} record')
        columns = ', '.join((*key_columns, 'payload'))
        marks = ', '.join('?' for _ in range(len(key_values) + 1))
        try:
            self._db.execute(f'INSERT INTO {table} ({columns}) VALUES ({marks})', (*key_values, payload))
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(f'could not append {table} record') from exc
        return True

    def append_snapshot(self, snapshot):
        data = snapshot.to_dict() if isinstance(snapshot, EvaluationSnapshot) else snapshot
        if not isinstance(data, dict):
            raise PersistenceError('snapshot must be an EvaluationSnapshot or dict')
        run = data.get('processing_run_id')
        seq = data.get('sequence')
        if parse_run_id(run) is None or type(seq) is not int or seq < 0:
            raise PersistenceError('invalid snapshot identity')
        if set(data) != set(EvaluationSnapshot.__dataclass_fields__):
            raise PersistenceError('invalid snapshot fields')
        payload = _json(data)
        with self._db:
            return self._insert('snapshots', ('processing_run_id', 'sequence'), (run, seq), payload)

    def load_snapshots(self, processing_run_id=None):
        if processing_run_id is not None and parse_run_id(processing_run_id) is None:
            raise PersistenceError('invalid processing_run_id')
        if processing_run_id is None:
            rows = self._db.execute('SELECT payload FROM snapshots ORDER BY processing_run_id, sequence')
        else:
            rows = self._db.execute('SELECT payload FROM snapshots WHERE processing_run_id=? ORDER BY sequence', (processing_run_id,))
        result = []
        for (payload,) in rows:
            data = _unjson(payload)
            result.append(EvaluationSnapshot(**{
                field: deep_freeze(value) if field in ('candidate_facts','verified_evidence','rule_result','processing_errors') else value
                for field, value in data.items()
            }))
        return tuple(result)

    def append_proposal(self, proposal):
        validate_correction_proposal(proposal)
        data = proposal.to_dict() if isinstance(proposal, CorrectionProposal) else dict(proposal)
        data['created_at'] = _utc_string(data['created_at'])
        payload = _json(data)
        with self._db:
            return self._insert('proposals', ('proposal_id',), (data['proposal_id'],), payload)

    def load_proposals(self):
        rows = self._db.execute('SELECT payload FROM proposals ORDER BY rowid').fetchall()
        result = []
        for (payload,) in rows:
            data = _unjson(payload)
            data['created_at'] = _decode_utc(data['created_at'])
            validate_correction_proposal(data)
            result.append(CorrectionProposal(**data))
        return tuple(result)

    def append_event(self, event):
        if not isinstance(event, CorrectionLifecycleEvent):
            raise PersistenceError('event must be a CorrectionLifecycleEvent')
        data = event.to_dict()
        if parse_run_id(data['event_id']) is None or parse_run_id(data['proposal_id']) is None:
            raise PersistenceError('invalid event identity')
        if not isinstance(data['actor_id'], str) or not data['actor_id'].strip():
            raise PersistenceError('invalid event actor')
        data['created_at'] = _utc_string(data['created_at'])
        payload = _json(data)
        with self._db:
            return self._insert('lifecycle_events', ('event_id', 'proposal_id'), (data['event_id'], data['proposal_id']), payload)

    def load_events(self):
        rows = self._db.execute('SELECT payload FROM lifecycle_events ORDER BY rowid').fetchall()
        result = []
        for (payload,) in rows:
            data = _unjson(payload)
            data['created_at'] = _decode_utc(data['created_at'])
            result.append(CorrectionLifecycleEvent(**data))
        return tuple(result)

    def save_ledger(self, ledger):
        """Atomically append all new proposals and events; never modify old rows."""
        if not isinstance(ledger, CorrectionProposalLedger):
            raise PersistenceError('expected CorrectionProposalLedger')
        # A single transaction prevents partially saved ledger history.
        with self._db:
            for proposal in ledger.proposals:
                data = proposal.to_dict()
                validate_correction_proposal(data)
                data['created_at'] = _utc_string(data['created_at'])
                self._insert('proposals', ('proposal_id',), (data['proposal_id'],), _json(data))
            for event in ledger.events:
                data = event.to_dict()
                data['created_at'] = _utc_string(data['created_at'])
                self._insert('lifecycle_events', ('event_id', 'proposal_id'), (data['event_id'], data['proposal_id']), _json(data))

    def load_ledger(self):
        """Restore a ledger from durable history without re-emitting events."""
        ledger = CorrectionProposalLedger()
        ledger._proposals = {p.proposal_id: p for p in self.load_proposals()}
        ledger._events = list(self.load_events())
        return ledger
