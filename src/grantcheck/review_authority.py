"""Reviewer identity and role validation for GrantCheck.

Reviewer records describe claimed identities and roles.
Validation does not authenticate a person or verify role assignment.
"""

REVIEWER_ROLES = ("staff_reviewer", "authorized_reviewer")
REVIEWER_FIELDS = ("reviewer_id", "roles")


class ReviewerValidationError(ValueError):
    """A reviewer record does not conform to the approved schema."""


def validate_reviewer(reviewer):
    """Validate a reviewer record without modifying it.

    Return None if valid; otherwise raise ReviewerValidationError.
    """
    if not isinstance(reviewer, dict):
        raise ReviewerValidationError("reviewer: expected dict")

    for field in REVIEWER_FIELDS:
        if field not in reviewer:
            raise ReviewerValidationError(
                f"reviewer.{field}: missing required field"
            )

    for field in reviewer:
        if field not in REVIEWER_FIELDS:
            raise ReviewerValidationError(
                f"reviewer.{field}: unexpected field"
            )

    reviewer_id = reviewer["reviewer_id"]
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ReviewerValidationError(
            "reviewer.reviewer_id: expected nonblank string"
        )

    roles = reviewer["roles"]
    if not isinstance(roles, (list, tuple)) or not roles:
        raise ReviewerValidationError(
            "reviewer.roles: expected nonempty list or tuple"
        )

    if any(not isinstance(role, str) or role not in REVIEWER_ROLES
           for role in roles):
        raise ReviewerValidationError(
            "reviewer.roles: unsupported role"
        )

    if len(set(roles)) != len(roles):
        raise ReviewerValidationError(
            "reviewer.roles: duplicate role"
        )

    return None


REVIEW_ACTIONS = (
    "view_evidence",
    "submit_correction",
    "decide_correction",
    "trigger_reevaluation",
    "complete_review",
    "reopen_review",
    "decide_grant_funding",
)

SHARED_ACTIONS = frozenset((
    "view_evidence",
    "submit_correction",
))

AUTHORIZED_ACTIONS = frozenset((
    "decide_correction",
    "trigger_reevaluation",
    "complete_review",
    "reopen_review",
))


class ReviewerAuthorizationError(PermissionError):
    """A valid reviewer lacks permission for an action."""


def check_review_permission(reviewer, action):
    """Check role-based permission without performing the action."""
    validate_reviewer(reviewer)

    if not isinstance(action, str) or action not in REVIEW_ACTIONS:
        raise ReviewerValidationError(
            "action: unsupported or invalid review action"
        )

    if action == "decide_grant_funding":
        raise ReviewerAuthorizationError(
            "action: grant funding decisions are prohibited"
        )

    if action in SHARED_ACTIONS:
        return None

    if "authorized_reviewer" in reviewer["roles"]:
        return None

    raise ReviewerAuthorizationError(
        f"action: reviewer lacks permission for {action}"
    )
