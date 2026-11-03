"""Read-only validation of GrantCheck candidate facts.

The authoritative vocabulary and document roles are defined in fact_schema.
"""

import math

from grantcheck.fact_schema import (
    FACT_ALLOWED_VALUES,
    FACT_SOURCES,
    FACT_STATUSES,
    FACT_VALUE_TYPES,
    SOURCE_RECORD_FIELDS,
)


class CandidateFactValidationError(ValueError):
    """Candidate facts do not conform to the approved schema."""


def _fail(path, reason):
    raise CandidateFactValidationError(f"{path}: {reason}")


def _check_keys(mapping, expected, path):
    expected = tuple(expected)
    for key in expected:
        if key not in mapping:
            _fail(f"{path}.{key}", "missing required key")
    for key in mapping:
        if key not in expected:
            _fail(f"{path}.{key}", "unexpected key")


def validate_candidate_facts(candidate_facts):
    """Validate all 12 source records without modifying the input.

    Return None if valid; otherwise raise CandidateFactValidationError
    describing the first failure in authoritative schema order.
    """
    if not isinstance(candidate_facts, dict):
        _fail("candidate_facts", "expected dict")

    _check_keys(candidate_facts, FACT_SOURCES, "candidate_facts")

    for category, roles in FACT_SOURCES.items():
        category_path = f"candidate_facts.{category}"
        records = candidate_facts[category]
        if not isinstance(records, dict):
            _fail(category_path, "expected dict")
        _check_keys(records, roles, category_path)

        for role in roles:
            record_path = f"{category_path}.{role}"
            record = records[role]
            if not isinstance(record, dict):
                _fail(record_path, "expected dict")
            _check_keys(record, SOURCE_RECORD_FIELDS, record_path)

            # Validate fields in the authoritative SOURCE_RECORD_FIELDS order.
            value = record["value"]
            status = record["status"]
            evidence = record["evidence_text"]
            if not isinstance(status, str) or status not in FACT_STATUSES:
                _fail(f"{record_path}.status", f"expected one of {FACT_STATUSES!r}")

            if status == "present":
                expected_type = FACT_VALUE_TYPES[category]
                if isinstance(value, bool) or not isinstance(value, expected_type):
                    _fail(f"{record_path}.value", f"invalid present value for {category}")
                if isinstance(value, float) and not math.isfinite(value):
                    _fail(f"{record_path}.value", "must be finite")

                if category == "project_duration_months" and value <= 0:
                    _fail(f"{record_path}.value", "must be a positive integer")
                if category in ("personnel_cost_usd", "travel_cost_usd") and value < 0:
                    _fail(f"{record_path}.value", "must be nonnegative")
                if category == "personnel_effort_percent" and not 0 <= value <= 100:
                    _fail(f"{record_path}.value", "must be between 0 and 100 inclusive")
                if category == "personnel_role" and not value.strip():
                    _fail(f"{record_path}.value", "must be nonblank")
                if category in FACT_ALLOWED_VALUES and value not in FACT_ALLOWED_VALUES[category]:
                    _fail(f"{record_path}.value", f"must be one of {FACT_ALLOWED_VALUES[category]!r}")
            elif value is not None:
                _fail(f"{record_path}.value", "must be None unless status is present")

            if status in ("present", "uncertain"):
                if not isinstance(evidence, str) or not evidence.strip():
                    _fail(f"{record_path}.evidence_text", "must be a nonblank string")
            elif evidence is not None:
                _fail(f"{record_path}.evidence_text", "must be None for missing, unprocessable, or not_found")

    return None
