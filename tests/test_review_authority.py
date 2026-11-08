"""Tests for GrantCheck human-reviewer record validation."""

from copy import deepcopy

import pytest

from grantcheck.review_authority import (
    ReviewerValidationError,
    validate_reviewer,
)


@pytest.mark.parametrize("roles", [
    ("staff_reviewer",),
    ("authorized_reviewer",),
    ("staff_reviewer", "authorized_reviewer"),
    ["staff_reviewer"],
    ["authorized_reviewer"],
    ["staff_reviewer", "authorized_reviewer"],
])
def test_valid_reviewer_roles(roles):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": roles,
    }
    before = deepcopy(reviewer)

    assert validate_reviewer(reviewer) is None
    assert reviewer == before


@pytest.mark.parametrize("reviewer_id", [
    "",
    " ",
    "\t\n",
    None,
    123,
    True,
    [],
])
def test_invalid_reviewer_ids(reviewer_id):
    reviewer = {
        "reviewer_id": reviewer_id,
        "roles": ("staff_reviewer",),
    }

    with pytest.raises(ReviewerValidationError, match="reviewer_id"):
        validate_reviewer(reviewer)


@pytest.mark.parametrize("roles", [
    (),
    [],
    ("unknown_role",),
    ("staff_reviewer", "staff_reviewer"),
    ["authorized_reviewer", "authorized_reviewer"],
    "staff_reviewer",
    None,
    123,
])
def test_invalid_roles(roles):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": roles,
    }

    with pytest.raises(ReviewerValidationError, match="roles"):
        validate_reviewer(reviewer)


@pytest.mark.parametrize("field", ["reviewer_id", "roles"])
def test_missing_required_fields(field):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("staff_reviewer",),
    }
    del reviewer[field]

    with pytest.raises(ReviewerValidationError, match=field):
        validate_reviewer(reviewer)


def test_unexpected_field_is_rejected():
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("staff_reviewer",),
        "is_admin": True,
    }

    with pytest.raises(ReviewerValidationError, match="is_admin"):
        validate_reviewer(reviewer)


@pytest.mark.parametrize("reviewer", [
    None,
    [],
    "reviewer_001",
    123,
])
def test_invalid_reviewer_structure(reviewer):
    with pytest.raises(ReviewerValidationError):
        validate_reviewer(reviewer)


def test_reviewer_id_is_preserved_exactly():
    reviewer = {
        "reviewer_id": "  Reviewer_001  ",
        "roles": ["staff_reviewer"],
    }
    before = deepcopy(reviewer)

    assert validate_reviewer(reviewer) is None
    assert reviewer == before


# Stage 6A.3 — Reviewer permission tests

from grantcheck.review_authority import (
    ReviewerAuthorizationError,
    check_review_permission,
)


@pytest.mark.parametrize("action", [
    "view_evidence",
    "submit_correction",
])
@pytest.mark.parametrize("role", [
    "staff_reviewer",
    "authorized_reviewer",
])
def test_shared_review_permissions(role, action):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": (role,),
    }
    before = deepcopy(reviewer)

    assert check_review_permission(reviewer, action) is None
    assert reviewer == before


@pytest.mark.parametrize("action", [
    "decide_correction",
    "trigger_reevaluation",
    "complete_review",
    "reopen_review",
])
def test_authorized_reviewer_permissions(action):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("authorized_reviewer",),
    }

    assert check_review_permission(reviewer, action) is None


@pytest.mark.parametrize("action", [
    "decide_correction",
    "trigger_reevaluation",
    "complete_review",
    "reopen_review",
])
def test_staff_reviewer_restricted_actions(action):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("staff_reviewer",),
    }

    with pytest.raises(ReviewerAuthorizationError):
        check_review_permission(reviewer, action)


@pytest.mark.parametrize("roles", [
    ("staff_reviewer",),
    ("authorized_reviewer",),
    ("staff_reviewer", "authorized_reviewer"),
])
def test_grant_funding_decision_is_prohibited(roles):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": roles,
    }

    with pytest.raises(ReviewerAuthorizationError):
        check_review_permission(reviewer, "decide_grant_funding")


def test_combined_roles_receive_authorized_permissions():
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("staff_reviewer", "authorized_reviewer"),
    }

    for action in (
        "view_evidence",
        "submit_correction",
        "decide_correction",
        "trigger_reevaluation",
        "complete_review",
        "reopen_review",
    ):
        assert check_review_permission(reviewer, action) is None


@pytest.mark.parametrize("action", [
    "",
    "unknown_action",
    None,
    123,
    ["view_evidence"],
])
def test_unknown_or_invalid_actions(action):
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ("authorized_reviewer",),
    }

    with pytest.raises(ReviewerValidationError, match="action"):
        check_review_permission(reviewer, action)


def test_permission_check_rejects_invalid_reviewer():
    reviewer = {
        "reviewer_id": "",
        "roles": ("authorized_reviewer",),
    }

    with pytest.raises(ReviewerValidationError, match="reviewer_id"):
        check_review_permission(reviewer, "view_evidence")


def test_denied_permission_does_not_modify_reviewer():
    reviewer = {
        "reviewer_id": "reviewer_001",
        "roles": ["staff_reviewer"],
    }
    before = deepcopy(reviewer)

    with pytest.raises(ReviewerAuthorizationError):
        check_review_permission(reviewer, "complete_review")

    assert reviewer == before
