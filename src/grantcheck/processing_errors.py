"""Structured processing-error records for the future extraction workflow.

This module defines the record schema, a strict validator, and controlled
failure responses. It is independent of the replay workflow: replay error
dictionaries are unchanged and are not validated here.

A processing-error record has exactly these fields:

    error_type          one of ERROR_TYPES
    document            a document role from DOCUMENT_ROLES, or None for an
                        application-level error
    fact_category       a fact category from FACT_SOURCES, or None for a
                        document-wide or application-level error
    processing_run_id   the authoritative run UUID (see grantcheck.run_identity)

Diagnostics never contain submitted values, document text, or exception
traces; they name only the record index, a schema field, and a reason code.
"""

from grantcheck.document_intake import DOCUMENT_ROLES
from grantcheck.fact_schema import FACT_SOURCES
from grantcheck.run_identity import RunIdentityError, new_processing_run_id, parse_run_id

ERROR_TYPES = ("document_processing_failure", "extraction_failure", "internal_processing_failure")

RECORD_FIELDS = ("error_type", "document", "fact_category", "processing_run_id")

# Diagnostic reason codes, in the fixed order used to sort diagnostics for one field.
DIAGNOSTIC_REASONS = (
    "invalid_record_structure",
    "unsupported_error_type",
    "unsupported_identifier",
    "invalid_scope_combination",
    "run_identity_mismatch",
)

FAILURE_CODES = ("invalid_processing_error_record", "application_integrity_failure", "fatal_internal_failure")

DIAGNOSTIC_FIELDS = ("record_index", "field", "reason")

# Diagnostics are sorted by record index (None, for the collection itself, comes
# first), then by this field order (None means the record as a whole), then by
# DIAGNOSTIC_REASONS order.
_FIELD_ORDER = (None,) + RECORD_FIELDS


def _diagnostic(record_index, field, reason):
    return {"record_index": record_index, "field": field, "reason": reason}


def _record_problems(record):
    """Yield (field, reason) pairs for one record. Values are never included."""
    if not isinstance(record, dict):
        yield None, "invalid_record_structure"
        return

    for field in RECORD_FIELDS:
        if field not in record:
            yield field, "invalid_record_structure"
    if any(key not in RECORD_FIELDS for key in record):
        # Unexpected keys are reported without naming them, since a key is submitted content.
        yield None, "invalid_record_structure"

    if "error_type" in record:
        error_type = record["error_type"]
        if not isinstance(error_type, str):
            yield "error_type", "invalid_record_structure"
        elif error_type not in ERROR_TYPES:
            yield "error_type", "unsupported_error_type"

    document = record.get("document")
    document_ok = document is None or (isinstance(document, str) and document in DOCUMENT_ROLES)
    if "document" in record and not document_ok:
        yield "document", "unsupported_identifier"

    category = record.get("fact_category")
    category_ok = category is None or (isinstance(category, str) and category in FACT_SOURCES)
    if "fact_category" in record and not category_ok:
        yield "fact_category", "unsupported_identifier"

    # Scope is checked only when both identifiers are individually valid.
    if "document" in record and "fact_category" in record and document_ok and category_ok and category is not None:
        if document is None or document not in FACT_SOURCES[category]:
            yield "fact_category", "invalid_scope_combination"


def validate_processing_errors(records, processing_run_id):
    """Validate a collection of processing-error records against the authoritative run ID.

    Returns a new dict:
        {"valid": bool, "evaluation_permitted": bool, "diagnostics": [...]}
    where each diagnostic is {"record_index", "field", "reason"}. Every safely
    identifiable problem is collected; nothing is repaired or rewritten, and
    the records are not modified. evaluation_permitted is False whenever any
    record is invalid, in which case no rule evaluation or R-03 snapshot may
    follow.

    Raises ValueError if processing_run_id is not a valid UUID string: the
    authoritative ID is a caller responsibility, not submitted data.
    """
    authoritative = parse_run_id(processing_run_id)
    if authoritative is None:
        raise ValueError("processing_run_id must be a valid authoritative UUID string")

    found = set()
    if not isinstance(records, (list, tuple)):
        found.add((None, None, "invalid_record_structure"))
    else:
        for index, record in enumerate(records):
            for field, reason in _record_problems(record):
                found.add((index, field, reason))
            if isinstance(record, dict) and "processing_run_id" in record:
                run_uuid = parse_run_id(record["processing_run_id"])
                if run_uuid is None:
                    found.add((index, "processing_run_id", "invalid_record_structure"))
                elif run_uuid != authoritative:
                    found.add((index, "processing_run_id", "run_identity_mismatch"))

    ordered = sorted(found, key=lambda item: (
        -1 if item[0] is None else item[0],
        _FIELD_ORDER.index(item[1]),
        DIAGNOSTIC_REASONS.index(item[2]),
    ))
    diagnostics = [_diagnostic(*item) for item in ordered]
    valid = not diagnostics
    return {"valid": valid, "evaluation_permitted": valid, "diagnostics": diagnostics}


def _sanitized_diagnostics(diagnostics):
    """Rebuild diagnostics from controlled vocabulary only; anything else is a caller error."""
    clean = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict) or set(diagnostic) != set(DIAGNOSTIC_FIELDS):
            raise ValueError("diagnostics must be validator diagnostics")
        index, field, reason = (diagnostic[key] for key in DIAGNOSTIC_FIELDS)
        index_ok = index is None or (isinstance(index, int) and not isinstance(index, bool) and index >= 0)
        if not index_ok or field not in _FIELD_ORDER or reason not in DIAGNOSTIC_REASONS:
            raise ValueError("diagnostics must be validator diagnostics")
        clean.append(_diagnostic(index, field, reason))
    return clean


def build_failure_response(failure_code, processing_run_id=None, diagnostics=()):
    """Return a controlled extraction failure response.

        {"processing_run_id": str or None, "failure_code": str,
         "rule_results": None, "diagnostics": [...]}

    processing_run_id must be the authoritative UUID string when one exists,
    or None (for example, when it could not be initialized). The response is
    separate from the replay result contract and never contains rejected
    values, document content, or exception traces.
    """
    if failure_code not in FAILURE_CODES:
        raise ValueError(f"failure_code must be one of {FAILURE_CODES}")
    if processing_run_id is not None and parse_run_id(processing_run_id) is None:
        raise ValueError("processing_run_id must be a valid UUID string or None")
    return {
        "processing_run_id": processing_run_id,
        "failure_code": failure_code,
        "rule_results": None,
        "diagnostics": _sanitized_diagnostics(diagnostics),
    }


def initialize_processing_run(uuid_factory=None):
    """Start an extraction run: return (processing_run_id, None) or (None, failure response).

    If the authoritative ID cannot be created, the second item is a
    fatal_internal_failure response with processing_run_id None; the caller
    must then stop before any document processing.
    """
    try:
        if uuid_factory is None:
            return new_processing_run_id(), None
        return new_processing_run_id(uuid_factory), None
    except RunIdentityError:
        return None, build_failure_response("fatal_internal_failure")
