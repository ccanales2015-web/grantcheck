
"""Stage 6B.1: Correction-proposal schema validation tests.

Scope:
- Validate proposal structure and field types.
- Validate category/role combinations.
- Validate operation-specific value requirements.
- Validate evidence-source structure.
- Reject unexpected fields and ambiguous timestamps.
- Ensure validation does not mutate its input.

Out of scope:
- Snapshot existence and original-value comparison (6B.2).
- Actual document and excerpt verification (6B.3).
- Reviewer authorization and proposal creation (6B.4/integration).
- Persistence and lifecycle events.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from grantcheck.correction_proposals import (
    CorrectionProposalValidationError,
    validate_correction_proposal,
)
from grantcheck.fact_schema import FACT_SOURCES


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

DOCUMENT_EVIDENCE_FIELDS = frozenset({
    "document_role",
    "document_sha256",
    "evidence_text",
    "start_offset",
    "end_offset",
})

HUMAN_ASSERTION_FIELDS = frozenset({"assertion_basis"})


# Category-specific examples. Values are intended to satisfy the
# current candidate-fact value contract.
VALID_VALUES = {
    "project_duration_months": (24, 18),
    "personnel_role": ("Researcher", "Engineer"),
    "personnel_cost_usd": (50000, 60000),
    "personnel_effort_percent": (40, 50),
    "travel_cost_usd": (2000, 3000),
    "travel_justification": ("adequate", "adequate"),
}


def make_valid_proposal(
    *,
    category="project_duration_months",
    role=None,
    operation="replace",
    source_type="document_evidence",
):
    """Build a valid example without accessing real snapshots."""
    if role is None:
        role = FACT_SOURCES[category][0]

    original, proposed = VALID_VALUES[category]

    if operation == "add":
        original = None
    elif operation == "invalidate":
        proposed = None

    if source_type == "document_evidence":
        excerpt = "The proposed project will last 18 months."
        source_details = {
            "document_role": "project_narrative",
            "document_sha256": "a" * 64,
            "evidence_text": excerpt,
            "start_offset": 120,
            "end_offset": 120 + len(excerpt),
        }
    else:
        source_details = {
            "assertion_basis": (
                "The reviewer reports receiving a revised timeline."
            ),
        }

    return {
        "proposal_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc),
        "reviewer_id": "reviewer_001",
        "processing_run_id": str(uuid4()),
        "snapshot_sequence": 0,
        "fact_category": category,
        "document_role": role,
        "operation": operation,
        "original_value": original,
        "proposed_value": proposed,
        "reason": "The extracted information requires correction.",
        "source_type": source_type,
        "source_details": source_details,
    }


def assert_invalid(proposal):
    with pytest.raises(CorrectionProposalValidationError):
        validate_correction_proposal(proposal)


# ============================================================
# A. Basic structure
# ============================================================

def test_valid_proposal_is_accepted():
    proposal = make_valid_proposal()
    assert validate_correction_proposal(proposal) is None


@pytest.mark.parametrize("invalid", [None, [], "text", 123])
def test_non_dictionary_proposal_is_rejected(invalid):
    assert_invalid(invalid)


@pytest.mark.parametrize("field", sorted(PROPOSAL_FIELDS))
def test_missing_required_field_is_rejected(field):
    proposal = make_valid_proposal()
    del proposal[field]
    assert_invalid(proposal)


def test_extra_field_is_rejected():
    proposal = make_valid_proposal()
    proposal["unexpected"] = True
    assert_invalid(proposal)

def test_mixed_type_unexpected_proposal_keys_are_rejected():
    proposal = make_valid_proposal()
    proposal[123] = "unexpected"

    assert_invalid(proposal)

def test_mixed_type_unexpected_evidence_keys_are_rejected():
    proposal = make_valid_proposal()
    proposal["source_details"][123] = "unexpected"

    assert_invalid(proposal)

def test_mutable_status_field_is_rejected():
    proposal = make_valid_proposal()
    proposal["status"] = "pending"
    assert_invalid(proposal)


def test_validation_does_not_mutate_input():
    proposal = make_valid_proposal()
    before = deepcopy(proposal)
    assert validate_correction_proposal(proposal) is None
    assert proposal == before


# ============================================================
# B. Identifiers and timestamps
# ============================================================

@pytest.mark.parametrize("field", [
    "proposal_id",
    "processing_run_id",
])
@pytest.mark.parametrize("invalid", [
    "", "not-a-uuid", None, 123, True,
])
def test_invalid_uuid_is_rejected(field, invalid):
    proposal = make_valid_proposal()
    proposal[field] = invalid
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    "", "   ", None, 123, True,
])
def test_invalid_reviewer_id_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["reviewer_id"] = invalid
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    -1, True, False, 1.5, "0", None,
])
def test_invalid_snapshot_sequence_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["snapshot_sequence"] = invalid
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    None,
    "2026-11-07T12:00:00Z",
    datetime(2026, 11, 7, 12, 0),
    datetime(
        2026, 11, 7, 7, 0,
        tzinfo=timezone(timedelta(hours=-5)),
    ),
    123,
])
def test_invalid_or_non_utc_timestamp_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["created_at"] = invalid
    assert_invalid(proposal)


def test_timezone_aware_utc_timestamp_is_accepted():
    proposal = make_valid_proposal()
    proposal["created_at"] = datetime(
        2026, 11, 7, 12, 0, tzinfo=timezone.utc
    )
    assert validate_correction_proposal(proposal) is None


# ============================================================
# C. Fact categories, roles, and operations
# ============================================================

@pytest.mark.parametrize("category,role", [
    (category, role)
    for category, roles in FACT_SOURCES.items()
    for role in roles
])
def test_all_defined_fact_slots_are_accepted(category, role):
    proposal = make_valid_proposal(
        category=category,
        role=role,
    )
    assert validate_correction_proposal(proposal) is None


@pytest.mark.parametrize("invalid", [
    "unknown_category", "", None, 123,
])
def test_unknown_fact_category_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["fact_category"] = invalid
    assert_invalid(proposal)


def test_disallowed_category_role_pair_is_rejected():
    proposal = make_valid_proposal()
    proposal["fact_category"] = "travel_justification"
    proposal["document_role"] = "budget"
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    "delete", "merge", "approve", "", None,
])
def test_unsupported_operation_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["operation"] = invalid
    assert_invalid(proposal)


# ============================================================
# D. Reason
# ============================================================

@pytest.mark.parametrize("invalid", [
    "", "   ", "\n\t", None, 123, True, [],
])
def test_invalid_reason_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["reason"] = invalid
    assert_invalid(proposal)


# ============================================================
# E. Evidence classification
# ============================================================

@pytest.mark.parametrize("source_type", [
    "document_evidence",
    "human_assertion",
])
def test_supported_source_types_are_accepted(source_type):
    proposal = make_valid_proposal(source_type=source_type)
    assert validate_correction_proposal(proposal) is None


@pytest.mark.parametrize("invalid", [
    "", "verified", "document", None, 123,
])
def test_unsupported_source_type_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["source_type"] = invalid
    assert_invalid(proposal)


def test_document_evidence_has_exact_fields():
    proposal = make_valid_proposal()
    assert set(proposal["source_details"]) == DOCUMENT_EVIDENCE_FIELDS
    assert validate_correction_proposal(proposal) is None


@pytest.mark.parametrize("field", sorted(DOCUMENT_EVIDENCE_FIELDS))
def test_missing_document_evidence_field_is_rejected(field):
    proposal = make_valid_proposal()
    del proposal["source_details"][field]
    assert_invalid(proposal)


def test_extra_document_evidence_field_is_rejected():
    proposal = make_valid_proposal()
    proposal["source_details"]["assertion_basis"] = "Claimed"
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    "", "xyz", "g" * 64, None, 123,
])
def test_invalid_document_hash_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["source_details"]["document_sha256"] = invalid
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    "", "   ", None, 123,
])
def test_invalid_evidence_excerpt_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["source_details"]["evidence_text"] = invalid
    assert_invalid(proposal)


@pytest.mark.parametrize("start,end", [
    (-1, 10),
    (10, 10),
    (11, 10),
    (True, 10),
    (0, False),
    ("0", 10),
    (0, None),
])
def test_invalid_evidence_offsets_are_rejected(start, end):
    proposal = make_valid_proposal()
    proposal["source_details"]["start_offset"] = start
    proposal["source_details"]["end_offset"] = end
    assert_invalid(proposal)


def test_offset_span_must_equal_excerpt_length():
    proposal = make_valid_proposal()
    proposal["source_details"]["end_offset"] += 1
    assert_invalid(proposal)


def test_human_assertion_has_exact_fields():
    proposal = make_valid_proposal(
        source_type="human_assertion"
    )
    assert set(proposal["source_details"]) == HUMAN_ASSERTION_FIELDS
    assert validate_correction_proposal(proposal) is None


@pytest.mark.parametrize("invalid", [
    "", "   ", None, 123,
])
def test_invalid_assertion_basis_is_rejected(invalid):
    proposal = make_valid_proposal(
        source_type="human_assertion"
    )
    proposal["source_details"]["assertion_basis"] = invalid
    assert_invalid(proposal)


def test_human_assertion_cannot_contain_document_hash():
    proposal = make_valid_proposal(
        source_type="human_assertion"
    )
    proposal["source_details"]["document_sha256"] = "a" * 64
    assert_invalid(proposal)


# ============================================================
# F. Operation-specific constraints
# ============================================================

@pytest.mark.parametrize("operation", [
    "replace", "add", "invalidate",
])
def test_supported_operations_are_accepted(operation):
    proposal = make_valid_proposal(operation=operation)
    assert validate_correction_proposal(proposal) is None


def test_replace_requires_proposed_value():
    proposal = make_valid_proposal(operation="replace")
    proposal["proposed_value"] = None
    assert_invalid(proposal)


def test_add_requires_proposed_value():
    proposal = make_valid_proposal(operation="add")
    proposal["proposed_value"] = None
    assert_invalid(proposal)


def test_add_requires_null_original_value():
    proposal = make_valid_proposal(operation="add")
    proposal["original_value"] = 24
    assert_invalid(proposal)


def test_invalidate_requires_null_proposed_value():
    proposal = make_valid_proposal(operation="invalidate")
    proposal["proposed_value"] = 18
    assert_invalid(proposal)


def test_invalidate_can_have_null_original_value():
    proposal = make_valid_proposal(operation="invalidate")
    proposal["original_value"] = None
    assert validate_correction_proposal(proposal) is None


def test_boolean_is_not_valid_duration():
    proposal = make_valid_proposal()
    proposal["proposed_value"] = True
    assert_invalid(proposal)


@pytest.mark.parametrize("invalid", [
    -1, 0, 1.5, "18",
])
def test_invalid_project_duration_is_rejected(invalid):
    proposal = make_valid_proposal()
    proposal["proposed_value"] = invalid
    assert_invalid(proposal)


def test_invalid_original_value_type_is_rejected():
    proposal = make_valid_proposal()
    proposal["original_value"] = ["not", "a", "duration"]
    assert_invalid(proposal)


def test_original_value_may_be_null():
    proposal = make_valid_proposal(operation="invalidate")
    proposal["original_value"] = None
    assert validate_correction_proposal(proposal) is None
