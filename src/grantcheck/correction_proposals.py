
"""GrantCheck correction-proposal validation and human-review workflow.

Implements the approved Stage 6B schema, snapshot-target checks, source-evidence
verification, immutable proposal creation, and append-only lifecycle events.
The prototype lifecycle ledger is in-memory; it does not provide durable
persistence or execute a reevaluation itself.
"""

import copy
import hashlib
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from grantcheck.immutable import deep_freeze, deep_thaw
from grantcheck.review_authority import (
    ReviewerAuthorizationError,
    check_review_permission,
    validate_reviewer,
)
from grantcheck.run_identity import parse_run_id, same_run

from grantcheck.fact_schema import (
    FACT_SOURCES,
    FACT_VALUE_TYPES,
    FACT_ALLOWED_VALUES,
)


class CorrectionProposalValidationError(ValueError):
    """A correction proposal violates the schema contract."""


PROPOSAL_FIELDS = frozenset({
    "proposal_id",
    "created_at",
    "reviewer_id",
    "processing_run_id",
    "snapshot_sequence",
    "fact_category",
    "document_role",
    "operation",
    "original_value",
    "proposed_value",
    "reason",
    "source_type",
    "source_details",
})

CORRECTION_OPERATIONS = frozenset({
    "replace",
    "add",
    "invalidate",
})

SOURCE_TYPES = frozenset({
    "document_evidence",
    "human_assertion",
})

DOCUMENT_EVIDENCE_FIELDS = frozenset({
    "document_role",
    "document_sha256",
    "evidence_text",
    "start_offset",
    "end_offset",
})

HUMAN_ASSERTION_FIELDS = frozenset({
    "assertion_basis",
})

def _format_keys_safely(keys):
    """Format dictionary keys deterministically without comparing key types."""
    return sorted(repr(key) for key in keys)

def _validate_proposal_structure(proposal):
    """Validate the proposal's container and exact field set."""
    if not isinstance(proposal, Mapping):
        raise CorrectionProposalValidationError(
            "proposal must be a mapping"
        )

    actual_fields = set(proposal)
    if actual_fields != PROPOSAL_FIELDS:
        missing = PROPOSAL_FIELDS - actual_fields
        unexpected = actual_fields - PROPOSAL_FIELDS

        raise CorrectionProposalValidationError(
            f"invalid proposal fields: "
            f"missing={sorted(missing)}, "
            f"unexpected={_format_keys_safely(unexpected)}"
        )

def _validate_identifiers(proposal):
    """Validate proposal identity and snapshot-reference fields."""
    for field in ("proposal_id", "processing_run_id"):
        value = proposal[field]

        if not isinstance(value, str):
            raise CorrectionProposalValidationError(
                f"{field} must be a UUID string"
            )

        if parse_run_id(value) is None:
            raise CorrectionProposalValidationError(
                f"{field} must be a canonical UUID string"
            )
        
    reviewer_id = proposal["reviewer_id"]
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise CorrectionProposalValidationError(
            "reviewer_id must be a nonblank string"
        )

    sequence = proposal["snapshot_sequence"]
    if type(sequence) is not int or sequence < 0:
        raise CorrectionProposalValidationError(
            "snapshot_sequence must be a nonnegative integer"
        )

def _validate_timestamp(proposal):
    """Require a timezone-aware UTC creation timestamp."""
    created_at = proposal["created_at"]

    if not isinstance(created_at, datetime):
        raise CorrectionProposalValidationError(
            "created_at must be a datetime object"
        )

    if (
        created_at.tzinfo is None
        or created_at.utcoffset() != timedelta(0)
    ):
        raise CorrectionProposalValidationError(
            "created_at must be timezone-aware and in UTC"
        )

def _validate_target_and_operation(proposal):
    """Validate the fact slot and requested correction operation."""
    category = proposal["fact_category"]
    role = proposal["document_role"]
    operation = proposal["operation"]

    if not isinstance(category, str) or category not in FACT_SOURCES:
        raise CorrectionProposalValidationError(
            "fact_category must be a recognized fact category"
        )

    if not isinstance(role, str) or role not in FACT_SOURCES[category]:
        raise CorrectionProposalValidationError(
            "document_role is not permitted for this fact category"
        )

    if not isinstance(operation, str) or operation not in CORRECTION_OPERATIONS:
        raise CorrectionProposalValidationError(
            "operation must be replace, add, or invalidate"
        )

def _validate_reason(proposal):
    """Require a nonblank explanation for the correction."""
    reason = proposal["reason"]

    if not isinstance(reason, str) or not reason.strip():
        raise CorrectionProposalValidationError(
            "reason must be a nonblank string"
        )

def _validate_evidence_classification(proposal):
    """Validate the source type and its required field structure."""
    source_type = proposal["source_type"]
    source_details = proposal["source_details"]

    if (
        not isinstance(source_type, str)
        or source_type not in SOURCE_TYPES
    ):
        raise CorrectionProposalValidationError(
            "source_type must be document_evidence or human_assertion"
        )

    if not isinstance(source_details, Mapping):
        raise CorrectionProposalValidationError(
            "source_details must be a mapping"
        )

    if source_type == "document_evidence":
        expected_fields = DOCUMENT_EVIDENCE_FIELDS
    else:
        expected_fields = HUMAN_ASSERTION_FIELDS

    actual_fields = set(source_details)

    if actual_fields != expected_fields:
        missing = expected_fields - actual_fields
        unexpected = actual_fields - expected_fields

        raise CorrectionProposalValidationError(
            f"invalid source_details fields: "
            f"missing={sorted(missing)}, "
            f"unexpected={_format_keys_safely(unexpected)}"
        )

def _validate_document_evidence(source_details):
    """Validate document-evidence fields without accessing documents."""
    role = source_details["document_role"]
    sha256 = source_details["document_sha256"]
    excerpt = source_details["evidence_text"]
    start = source_details["start_offset"]
    end = source_details["end_offset"]

    if not isinstance(role, str) or role not in {
        source_role
        for roles in FACT_SOURCES.values()
        for source_role in roles
    }:
        raise CorrectionProposalValidationError(
            "evidence document_role must be a recognized document role"
        )

    if (
        not isinstance(sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None
    ):
        raise CorrectionProposalValidationError(
            "document_sha256 must be a 64-character hexadecimal string"
        )

    if not isinstance(excerpt, str) or not excerpt.strip():
        raise CorrectionProposalValidationError(
            "evidence_text must be a nonblank string"
        )

    if (
        type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
    ):
        raise CorrectionProposalValidationError(
            "evidence offsets must be nonnegative integers "
            "with end_offset greater than start_offset"
        )

    if end - start != len(excerpt):
        raise CorrectionProposalValidationError(
            "evidence offset span must equal evidence_text length"
        )

def _validate_human_assertion(source_details):
    """Require a nonblank explanation supporting a human assertion."""
    assertion_basis = source_details["assertion_basis"]

    if (
        not isinstance(assertion_basis, str)
        or not assertion_basis.strip()
    ):
        raise CorrectionProposalValidationError(
            "assertion_basis must be a nonblank string"
        )

def _validate_fact_value(category, value, field):
    """Validate a non-null fact value against its category constraints."""
    expected_type = FACT_VALUE_TYPES[category]
    if isinstance(value, float) and not math.isfinite(value):
        raise CorrectionProposalValidationError(
            f"{field} must be finite"
    )

    if isinstance(value, bool) or not isinstance(value, expected_type):
        raise CorrectionProposalValidationError(
            f"{field} has an invalid type for {category}"
        )

    if isinstance(value, str) and not value.strip():
        raise CorrectionProposalValidationError(
            f"{field} must not be blank"
        )

    if category == "project_duration_months" and value <= 0:
        raise CorrectionProposalValidationError(
            f"{field} must be a positive duration"
        )

    if category in (
        "personnel_cost_usd",
        "travel_cost_usd",
    ) and value < 0:
        raise CorrectionProposalValidationError(
            f"{field} must not be negative"
        )

    if category == "personnel_effort_percent" and not (0 <= value <= 100):
        raise CorrectionProposalValidationError(
            f"{field} must be between 0 and 100"
        )

    allowed_values = FACT_ALLOWED_VALUES.get(category)
    if allowed_values is not None and value not in allowed_values:
        raise CorrectionProposalValidationError(
            f"{field} is not an allowed value for {category}"
        )

def _validate_operation_values(proposal):
    """Validate original and proposed values for the operation."""
    category = proposal["fact_category"]
    operation = proposal["operation"]
    original = proposal["original_value"]
    proposed = proposal["proposed_value"]

    # Validate the original value's basic constraints when present.
    # Whether it matches the snapshot is checked in Stage 6B.2.
    if original is not None:
        _validate_fact_value(category, original, "original_value")

    if operation == "replace":
        if proposed is None:
            raise CorrectionProposalValidationError(
                "replace requires a proposed_value"
            )
        _validate_fact_value(category, proposed, "proposed_value")

    elif operation == "add":
        if original is not None:
            raise CorrectionProposalValidationError(
                "add requires original_value to be None"
            )
        if proposed is None:
            raise CorrectionProposalValidationError(
                "add requires a proposed_value"
            )
        _validate_fact_value(category, proposed, "proposed_value")

    elif operation == "invalidate":
        if proposed is not None:
            raise CorrectionProposalValidationError(
                "invalidate requires proposed_value to be None"
            )

def validate_correction_proposal(proposal):
    """Validate a correction proposal without modifying it.

    Returns None when valid.

    Raises CorrectionProposalValidationError when the proposal
    violates the Stage 6B.1 schema contract.

    Does not verify snapshots, document contents, reviewer
    permissions, or persistence.
    """
    _validate_proposal_structure(proposal)
    _validate_identifiers(proposal)
    _validate_timestamp(proposal)
    _validate_target_and_operation(proposal)
    _validate_reason(proposal)
    _validate_evidence_classification(proposal)

    if proposal["source_type"] == "document_evidence":
        _validate_document_evidence(proposal["source_details"])
    else:
        _validate_human_assertion(proposal["source_details"])

    _validate_operation_values(proposal)

    return None

# ---------------------------------------------------------------------------
# Stage 6B.2 — Snapshot-target verification
# ---------------------------------------------------------------------------

class SnapshotVerificationError(CorrectionProposalValidationError):
    """A proposal does not refer to a valid evaluation snapshot or target."""


class StaleCorrectionProposalError(SnapshotVerificationError):
    """A newer evaluation snapshot exists; the correction must be reexamined."""


class CorrectionEvidenceError(CorrectionProposalValidationError):
    """Document evidence does not match the ingested document snapshot."""


class ProposalWorkflowError(ValueError):
    """A correction-proposal lifecycle operation is not permitted."""


ADDABLE_STATUSES = frozenset({
    "missing",
    "not_found",
    "uncertain",
    "unprocessable",
})


def _snapshot_value(snapshot, field):
    if isinstance(snapshot, Mapping):
        return snapshot.get(field)
    return getattr(snapshot, field, None)


def _snapshot_target_record(snapshot, category, role):
    facts = _snapshot_value(snapshot, "candidate_facts")
    if not isinstance(facts, Mapping):
        raise SnapshotVerificationError("snapshot candidate_facts must be a mapping")
    category_facts = facts.get(category)
    if not isinstance(category_facts, Mapping):
        raise SnapshotVerificationError(
            f"snapshot is missing candidate-fact category {category!r}"
        )
    record = category_facts.get(role)
    if not isinstance(record, Mapping):
        raise SnapshotVerificationError(
            f"snapshot is missing candidate-fact slot {category}.{role}"
        )
    required = {"value", "status", "evidence_text"}
    if set(record) != required:
        raise SnapshotVerificationError(
            f"snapshot candidate-fact slot {category}.{role} has an invalid schema"
        )
    return record


def _matching_snapshots(processing_run_id, snapshots):
    if isinstance(snapshots, (str, bytes, Mapping)):
        raise SnapshotVerificationError("snapshots must be an iterable of snapshot records")
    try:
        records = tuple(snapshots)
    except TypeError as error:
        raise SnapshotVerificationError("snapshots must be an iterable of snapshot records") from error

    matches = []
    seen_sequences = set()
    for snapshot in records:
        run_id = _snapshot_value(snapshot, "processing_run_id")
        sequence = _snapshot_value(snapshot, "sequence")
        if not same_run(run_id, processing_run_id):
            continue
        if type(sequence) is not int or sequence < 0:
            raise SnapshotVerificationError("snapshot sequence must be a nonnegative integer")
        if sequence in seen_sequences:
            raise SnapshotVerificationError(
                f"duplicate snapshot sequence {sequence} for processing run"
            )
        seen_sequences.add(sequence)
        matches.append(snapshot)

    if not matches:
        raise SnapshotVerificationError(
            "no evaluation snapshots exist for the proposal processing_run_id"
        )
    return matches


def latest_snapshot_for_run(processing_run_id, snapshots):
    """Return the highest-sequence snapshot for one canonical processing run."""
    if parse_run_id(processing_run_id) is None:
        raise SnapshotVerificationError("processing_run_id must be a canonical UUID string")
    matches = _matching_snapshots(processing_run_id, snapshots)
    return max(matches, key=lambda item: _snapshot_value(item, "sequence"))


def verify_correction_target(proposal, snapshots):
    """Verify the proposal targets the latest snapshot and its recorded fact value.

    A stale proposal is rejected for reexamination; it is never silently
    rebased onto a newer snapshot. The 12 category/role slots remain the
    authoritative target vocabulary.
    """
    validate_correction_proposal(proposal)
    matches = _matching_snapshots(proposal["processing_run_id"], snapshots)
    latest = max(matches, key=lambda item: _snapshot_value(item, "sequence"))
    latest_sequence = _snapshot_value(latest, "sequence")
    proposal_sequence = proposal["snapshot_sequence"]

    if proposal_sequence < latest_sequence:
        raise StaleCorrectionProposalError(
            f"proposal references stale snapshot sequence {proposal_sequence}, "
            f"but latest sequence is {latest_sequence}; reexamination is required"
        )
    if proposal_sequence > latest_sequence:
        raise SnapshotVerificationError(
            f"proposal references nonexistent future snapshot sequence {proposal_sequence}; "
            f"latest sequence is {latest_sequence}"
        )

    selected = [
        item for item in matches
        if _snapshot_value(item, "sequence") == proposal_sequence
    ]
    if len(selected) != 1:
        raise SnapshotVerificationError("the referenced evaluation snapshot is ambiguous")

    snapshot = selected[0]
    record = _snapshot_target_record(
        snapshot, proposal["fact_category"], proposal["document_role"]
    )
    status = record.get("status")
    if (
        not isinstance(status, str)
        or status not in {"present", "missing", "not_found", "uncertain", "unprocessable"}
    ):
        raise SnapshotVerificationError("snapshot target has an unsupported fact status")

    operation = proposal["operation"]
    original = proposal["original_value"]

    if operation in {"replace", "invalidate"}:
        if status != "present":
            raise SnapshotVerificationError(
                f"{operation} requires a present candidate fact; snapshot status is {status!r}"
            )
        if original != record.get("value"):
            raise SnapshotVerificationError(
                "original_value does not match the referenced snapshot candidate fact"
            )
    elif operation == "add":
        if status not in ADDABLE_STATUSES:
            raise SnapshotVerificationError(
                f"add requires a non-present target; snapshot status is {status!r}"
            )
        if original is not None:
            raise SnapshotVerificationError("add requires original_value to be None")

    return None


# ---------------------------------------------------------------------------
# Stage 6B.3 — Evidence verification
# ---------------------------------------------------------------------------

def verify_correction_evidence(proposal, application):
    """Verify document evidence against the exact text and hash captured at intake.

    Human assertions remain explicitly classified as human assertions. Their
    stated basis is structurally validated but is not represented as document
    evidence or independently certified by this function.
    """
    validate_correction_proposal(proposal)
    if proposal["source_type"] == "human_assertion":
        return None

    if not isinstance(application, Mapping):
        raise CorrectionEvidenceError("application must be a mapping")
    if application.get("intake_status") != "completed":
        raise CorrectionEvidenceError("application intake must be completed")

    documents = application.get("documents")
    if not isinstance(documents, Mapping):
        raise CorrectionEvidenceError("application documents must be a mapping")

    details = proposal["source_details"]
    role = details["document_role"]
    document = documents.get(role)
    if not isinstance(document, Mapping):
        raise CorrectionEvidenceError(
            f"ingested document {role!r} is unavailable"
        )

    filename = document.get("filename")
    text = document.get("text")
    stored_hash = document.get("sha256")
    if (
        not isinstance(filename, str) or not filename.strip()
        or "/" in filename or "\\" in filename
    ):
        raise CorrectionEvidenceError("ingested document filename is invalid")
    if not isinstance(text, str):
        raise CorrectionEvidenceError("ingested document text is invalid")
    if not isinstance(stored_hash, str):
        raise CorrectionEvidenceError("ingested document SHA-256 is invalid")

    actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    supplied_hash = details["document_sha256"]
    if stored_hash.lower() != actual_hash or supplied_hash.lower() != actual_hash:
        raise CorrectionEvidenceError(
            "document SHA-256 does not match the document captured at intake"
        )

    start = details["start_offset"]
    end = details["end_offset"]
    excerpt = details["evidence_text"]
    if text[start:end] != excerpt:
        raise CorrectionEvidenceError(
            "evidence_text does not match the exact document text at the supplied offsets"
        )
    return None


def verify_correction_proposal(proposal, snapshots, application):
    """Run schema, latest-snapshot, original-value, and evidence verification."""
    validate_correction_proposal(proposal)
    verify_correction_target(proposal, snapshots)
    verify_correction_evidence(proposal, application)
    return None


# ---------------------------------------------------------------------------
# Stage 6B.4 — Immutable proposal creation and append-only lifecycle events
# ---------------------------------------------------------------------------

_PROPOSAL_FIELD_ORDER = (
    "proposal_id",
    "created_at",
    "reviewer_id",
    "processing_run_id",
    "snapshot_sequence",
    "fact_category",
    "document_role",
    "operation",
    "original_value",
    "proposed_value",
    "reason",
    "source_type",
    "source_details",
)


@dataclass(frozen=True)
class CorrectionProposal(Mapping):
    """Immutable, single-target correction proposal with the approved 13 fields."""

    proposal_id: str
    created_at: datetime
    reviewer_id: str
    processing_run_id: str
    snapshot_sequence: int
    fact_category: str
    document_role: str
    operation: str
    original_value: object
    proposed_value: object
    reason: str
    source_type: str
    source_details: Mapping

    def __post_init__(self):
        object.__setattr__(
            self, "source_details",
            deep_freeze(copy.deepcopy(dict(self.source_details)))
        )

    def __getitem__(self, key):
        if key not in _PROPOSAL_FIELD_ORDER:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(_PROPOSAL_FIELD_ORDER)

    def __len__(self):
        return len(_PROPOSAL_FIELD_ORDER)

    def to_dict(self):
        """Return a detached mutable export; it cannot mutate this proposal."""
        return {
            field: deep_thaw(getattr(self, field))
            for field in _PROPOSAL_FIELD_ORDER
        }


@dataclass(frozen=True)
class CorrectionLifecycleEvent:
    """Immutable lifecycle fact kept separately from the proposal record."""

    event_id: str
    proposal_id: str
    event_type: str
    actor_id: str
    created_at: datetime
    details: Mapping

    def __post_init__(self):
        object.__setattr__(
            self, "details",
            deep_freeze(copy.deepcopy(dict(self.details)))
        )

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "proposal_id": self.proposal_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "details": deep_thaw(self.details),
        }


def _as_immutable_proposal(proposal):
    if isinstance(proposal, CorrectionProposal):
        validate_correction_proposal(proposal)
        return proposal
    validate_correction_proposal(proposal)
    return CorrectionProposal(**{field: proposal[field] for field in _PROPOSAL_FIELD_ORDER})


def _utc_now(clock):
    value = clock()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ProposalWorkflowError("clock must return a timezone-aware UTC datetime")
    return value


def _new_uuid(uuid_factory, field):
    try:
        value = uuid_factory()
    except Exception as error:  # noqa: BLE001
        raise ProposalWorkflowError(f"{field} could not be generated") from error
    if not isinstance(value, uuid.UUID):
        raise ProposalWorkflowError(f"{field} factory must return uuid.UUID")
    return str(value)


def create_correction_proposal(
    reviewer,
    processing_run_id,
    snapshots,
    application,
    *,
    fact_category,
    document_role,
    operation,
    proposed_value,
    reason,
    source_type,
    source_details,
    uuid_factory=uuid.uuid4,
    clock=lambda: datetime.now(timezone.utc),
):
    """Create and verify one immutable proposal against the latest snapshot.

    The original value and snapshot sequence are taken from the latest snapshot,
    not trusted from the caller. The function does not apply the correction or
    modify the snapshot, application, or source documents.
    """
    validate_reviewer(reviewer)
    check_review_permission(reviewer, "submit_correction")
    snapshots = tuple(snapshots)
    snapshot = latest_snapshot_for_run(processing_run_id, snapshots)
    sequence = _snapshot_value(snapshot, "sequence")

    if not isinstance(fact_category, str) or fact_category not in FACT_SOURCES:
        raise CorrectionProposalValidationError(
            "fact_category must be a recognized fact category"
        )
    if not isinstance(document_role, str) or document_role not in FACT_SOURCES[fact_category]:
        raise CorrectionProposalValidationError(
            "document_role is not permitted for this fact category"
        )

    record = _snapshot_target_record(snapshot, fact_category, document_role)
    status = record.get("status")
    original_value = record.get("value") if status == "present" else None

    proposal_data = {
        "proposal_id": _new_uuid(uuid_factory, "proposal_id"),
        "created_at": _utc_now(clock),
        "reviewer_id": reviewer["reviewer_id"],
        "processing_run_id": processing_run_id,
        "snapshot_sequence": sequence,
        "fact_category": fact_category,
        "document_role": document_role,
        "operation": operation,
        "original_value": original_value,
        "proposed_value": proposed_value,
        "reason": reason,
        "source_type": source_type,
        "source_details": source_details,
    }
    validate_correction_proposal(proposal_data)
    proposal = CorrectionProposal(**proposal_data)
    verify_correction_target(proposal, snapshots)
    verify_correction_evidence(proposal, application)
    return proposal


class CorrectionProposalLedger:
    """In-memory append-only proposal and lifecycle-event ledger.

    Proposal records are immutable and never overwritten. Withdrawal, decision,
    overlap, and reevaluation-request facts are separate immutable events.
    This prototype ledger is intentionally in-memory; it does not claim durable
    persistence across process restarts.
    """

    def __init__(
        self,
        *,
        uuid_factory=uuid.uuid4,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self._uuid_factory = uuid_factory
        self._clock = clock
        self._proposals = {}
        self._events = []

    @property
    def proposals(self):
        return tuple(self._proposals.values())

    @property
    def events(self):
        return tuple(self._events)

    def _append_event(self, proposal_id, event_type, actor_id, details=None):
        event = CorrectionLifecycleEvent(
            event_id=_new_uuid(self._uuid_factory, "event_id"),
            proposal_id=proposal_id,
            event_type=event_type,
            actor_id=actor_id,
            created_at=_utc_now(self._clock),
            details=details or {},
        )
        self._events.append(event)
        return event

    def get_proposal(self, proposal_id):
        try:
            return self._proposals[proposal_id]
        except KeyError:
            raise ProposalWorkflowError("proposal does not exist") from None

    def get_status(self, proposal_id):
        self.get_proposal(proposal_id)
        status = "pending"
        for event in self._events:
            if event.proposal_id != proposal_id:
                continue
            if event.event_type == "proposal_withdrawn":
                status = "withdrawn"
            elif event.event_type == "correction_decided":
                status = event.details["decision"]
        return status

    def overlap_flags(self, proposal_id):
        self.get_proposal(proposal_id)
        return tuple(
            event for event in self._events
            if event.proposal_id == proposal_id and event.event_type == "overlap_flagged"
        )

    def submit(self, proposal):
        proposal = _as_immutable_proposal(proposal)
        if proposal.proposal_id in self._proposals:
            raise ProposalWorkflowError("proposal_id has already been submitted")

        overlapping = [
            existing for existing in self._proposals.values()
            if self.get_status(existing.proposal_id) == "pending"
            and same_run(existing.processing_run_id, proposal.processing_run_id)
            and existing.fact_category == proposal.fact_category
            and existing.document_role == proposal.document_role
        ]

        self._proposals[proposal.proposal_id] = proposal
        self._append_event(
            proposal.proposal_id, "proposal_submitted", proposal.reviewer_id
        )

        # Overlap is flagged, not silently merged or automatically rejected.
        for existing in overlapping:
            self._append_event(
                proposal.proposal_id,
                "overlap_flagged",
                proposal.reviewer_id,
                {"related_proposal_id": existing.proposal_id},
            )
            self._append_event(
                existing.proposal_id,
                "overlap_flagged",
                proposal.reviewer_id,
                {"related_proposal_id": proposal.proposal_id},
            )
        return proposal

    def withdraw(self, proposal_id, reviewer):
        validate_reviewer(reviewer)
        proposal = self.get_proposal(proposal_id)
        if reviewer["reviewer_id"] != proposal.reviewer_id:
            raise ReviewerAuthorizationError(
                "only the proposing reviewer may withdraw this proposal"
            )
        if self.get_status(proposal_id) != "pending":
            raise ProposalWorkflowError("only a pending proposal may be withdrawn")
        return self._append_event(
            proposal_id, "proposal_withdrawn", reviewer["reviewer_id"]
        )

    def decide(self, proposal_id, reviewer, decision):
        check_review_permission(reviewer, "decide_correction")
        if decision not in {"accepted", "rejected"}:
            raise ProposalWorkflowError("decision must be accepted or rejected")
        self.get_proposal(proposal_id)
        if self.get_status(proposal_id) != "pending":
            raise ProposalWorkflowError("only a pending proposal may be decided")
        return self._append_event(
            proposal_id,
            "correction_decided",
            reviewer["reviewer_id"],
            {"decision": decision},
        )

    def request_reevaluation(self, proposal_id, reviewer):
        check_review_permission(reviewer, "trigger_reevaluation")
        self.get_proposal(proposal_id)
        if self.get_status(proposal_id) != "accepted":
            raise ProposalWorkflowError(
                "reevaluation may be requested only for an accepted correction"
            )
        if any(
            event.proposal_id == proposal_id
            and event.event_type == "reevaluation_requested"
            for event in self._events
        ):
            raise ProposalWorkflowError("reevaluation has already been requested")
        return self._append_event(
            proposal_id, "reevaluation_requested", reviewer["reviewer_id"]
        )

