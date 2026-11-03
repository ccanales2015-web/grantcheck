"""Provenance verification and safe isolation for extracted duration facts.

verify_duration_extraction() checks the output of
duration_extraction.extract_candidate_facts() against the text stored at
document intake. Every occurrence is verified at its recorded offsets; the
text is never searched again, so a matching substring elsewhere in the
document (for example "Duration: 24 months" inside "Project Duration: 24
months") is not accepted as a source location.

A fact whose evidence or record cannot be trusted is isolated: it becomes
"unprocessable" with null value and evidence, a fact-scoped
extraction_failure processing-error record is produced, and the rejected
material is preserved as a restricted, immutable DiagnosticAttempt. Other
facts are verified independently. Problems that cannot be tied to one fact
produce an application-integrity indication instead.

The replay provenance resolver (grantcheck.provenance) is not used or changed.
Inputs are never modified, and no files are read.
"""

import copy
import uuid
from dataclasses import dataclass

from grantcheck.duration_extraction import (
    DURATION_CATEGORY,
    DURATION_LABELS,
    MATCH_AMBIGUOUS,
    MATCH_VALID,
    normalize_duration_quantity,
)
from grantcheck.fact_schema import FACT_SOURCES, NOT_FOUND_REASON_CODES
from grantcheck.fact_validation import CandidateFactValidationError, validate_candidate_facts
from grantcheck.immutable import deep_freeze, deep_thaw
from grantcheck.processing_errors import validate_processing_errors
from grantcheck.run_identity import RunIdentityError, parse_run_id

OCCURRENCE_KEYS = ("document", "fact_category", "excerpt", "start_offset", "end_offset",
                   "start_line", "match", "value")
EXTRACTION_KEYS = ("candidate_facts", "not_found_reasons", "duration_evidence")
DURATION_ROLES = FACT_SOURCES[DURATION_CATEGORY]

# Controlled reasons recorded on diagnostic attempts (never on public errors).
ATTEMPT_REASONS = (
    "malformed_occurrence",
    "duplicate_occurrence",
    "document_mismatch",
    "unsupported_fact_category",
    "invalid_offsets",
    "excerpt_mismatch",
    "not_a_statement",
    "line_mismatch",
    "unsupported_match",
    "invalid_normalized_value",
    "record_evidence_mismatch",
    "unsupported_fact_extraction",
    "application_integrity",
)

UNVERIFIED = "unverified"


@dataclass(frozen=True, repr=False)
class DiagnosticAttempt:
    """A restricted, immutable record of an extraction attempt that failed verification.

    Its contents are unverified and must never be used as rule evidence. They
    are not shown by repr() and cannot be serialized by accident (json.dumps
    raises TypeError); use to_export_dict() for controlled export.
    """

    attempt_id: str
    processing_run_id: str
    document: object          # a document role, or None if not identifiable
    fact_category: object     # a fact category, or None if not identifiable
    reason: str
    contents: object          # frozen copy of the original unverified material
    verification_status: str = UNVERIFIED

    def __repr__(self):
        return f"DiagnosticAttempt(attempt_id={self.attempt_id!r}, reason={self.reason!r}, contents=<restricted>)"

    def to_export_dict(self):
        """Plain, JSON-compatible copy for a deliberate, controlled export."""
        return {
            "attempt_id": self.attempt_id,
            "processing_run_id": self.processing_run_id,
            "document": self.document,
            "fact_category": self.fact_category,
            "reason": self.reason,
            "verification_status": self.verification_status,
            "contents": deep_thaw(self.contents),
        }


class _Attempts:
    """Creates diagnostic attempts with unique, never-reused UUIDs."""

    def __init__(self, processing_run_id, attempt_id_factory):
        self._run_id = processing_run_id
        self._factory = attempt_id_factory
        self._seen = {parse_run_id(processing_run_id)}
        self.items = []

    def add(self, document, fact_category, reason, contents):
        try:
            value = self._factory()
        except Exception as error:  # noqa: BLE001 - no attempt may exist without an identity
            raise RunIdentityError("diagnostic attempt identifier could not be initialized") from error
        if not isinstance(value, uuid.UUID) or value in self._seen:
            raise RunIdentityError("diagnostic attempt identifier could not be initialized")
        self._seen.add(value)
        self.items.append(DiagnosticAttempt(str(value), self._run_id, document, fact_category,
                                            reason, deep_freeze(copy.deepcopy(contents))))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _occurrence_problem(occurrence, role, text):
    """The first verification failure for one occurrence, or None if it is trustworthy."""
    if not isinstance(occurrence, dict) or set(occurrence) != set(OCCURRENCE_KEYS):
        return "malformed_occurrence"
    if occurrence["document"] != role:
        return "document_mismatch"
    if occurrence["fact_category"] != DURATION_CATEGORY:
        return "unsupported_fact_category"
    start, end = occurrence["start_offset"], occurrence["end_offset"]
    if not (_is_int(start) and _is_int(end) and 0 <= start < end <= len(text)):
        return "invalid_offsets"
    excerpt = occurrence["excerpt"]
    if not isinstance(excerpt, str) or text[start:end] != excerpt:
        return "excerpt_mismatch"
    if not _is_whole_statement(text, start, end, excerpt):
        return "not_a_statement"
    line = occurrence["start_line"]
    if not _is_int(line) or line != text.count("\n", 0, start) + 1:
        return "line_mismatch"
    match, value = occurrence["match"], occurrence["value"]
    if match not in (MATCH_VALID, MATCH_AMBIGUOUS):
        return "unsupported_match"
    if match == MATCH_VALID and not (_is_int(value) and value > 0):
        return "invalid_normalized_value"
    if match == MATCH_AMBIGUOUS and value is not None:
        return "invalid_normalized_value"
    # The claimed classification and value must follow from the verified excerpt itself.
    label = next(label for label in DURATION_LABELS if excerpt.startswith(label))
    months = normalize_duration_quantity(excerpt[len(label):])
    if (match == MATCH_VALID and months != value) or (match == MATCH_AMBIGUOUS and months is not None):
        return "invalid_normalized_value"
    return None


def _is_whole_statement(text, start, end, excerpt):
    """True if the span is a complete duration statement on its own line.

    Only spaces or tabs may precede the label on the line, and only spaces or
    tabs may follow the excerpt before the line break, so a substring inside a
    longer statement (such as "Duration:" within "Project Duration:") is rejected.
    """
    if not any(excerpt.startswith(label) for label in DURATION_LABELS):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    before = text[line_start:start]
    after = text[end:line_end].rstrip("\r")
    return before.strip(" \t") == "" and after.strip(" \t") == ""


def _record_supported(record, occurrences):
    """Whether verified occurrences justify the candidate-fact record exactly as reported."""
    status = record["status"]
    excerpts = {occurrence["excerpt"] for occurrence in occurrences}
    if status in ("not_found", "unprocessable"):
        return not occurrences
    if not occurrences or record["evidence_text"] not in excerpts:
        return False
    ambiguous = any(occurrence["match"] == MATCH_AMBIGUOUS for occurrence in occurrences)
    values = {occurrence["value"] for occurrence in occurrences if occurrence["match"] == MATCH_VALID}
    if status == "present":
        return not ambiguous and values == {record["value"]}
    if status == "uncertain":
        return ambiguous or len(values) > 1
    return False  # "missing" is never produced by structured extraction


def _error(document, fact_category, processing_run_id, error_type="extraction_failure"):
    return {"error_type": error_type, "document": document, "fact_category": fact_category,
            "processing_run_id": processing_run_id}


def _unprocessable():
    return {"value": None, "status": "unprocessable", "evidence_text": None}


def _application_failure(attempts, processing_run_id):
    attempts.add(None, None, "application_integrity", None)
    return {
        "application_integrity_failure": True,
        "candidate_facts": None,
        "not_found_reasons": None,
        "verified_evidence": None,
        "processing_errors": [_error(None, None, processing_run_id, "internal_processing_failure")],
        "diagnostic_attempts": tuple(attempts.items),
    }


def _document_texts(application):
    if not isinstance(application, dict) or not isinstance(application.get("documents"), dict):
        return None
    texts = {}
    for role in dict.fromkeys(role for roles in FACT_SOURCES.values() for role in roles):
        document = application["documents"].get(role)
        if not isinstance(document, dict) or not isinstance(document.get("text"), str):
            return None
        texts[role] = document["text"]
    return texts


def verify_duration_extraction(application, extraction, processing_run_id, attempt_id_factory=uuid.uuid4):
    """Verify extracted duration evidence and isolate facts whose provenance cannot be trusted.

    application is the document-intake record the extraction was produced
    from; extraction is the dict returned by extract_candidate_facts();
    processing_run_id is the authoritative run UUID (never replaced).
    attempt_id_factory is injectable for deterministic tests.

    Returns a new dict:
        {
            "application_integrity_failure": bool,
            "candidate_facts":   12 records, isolated facts "unprocessable"
                                 (None on an application-integrity failure),
            "not_found_reasons": reasons for records that are still not_found,
            "verified_evidence": {role: [verified occurrence, ...]}; empty for
                                 isolated facts, which supply no rule evidence,
            "processing_errors": public records in the Increment 1C schema,
            "diagnostic_attempts": tuple of restricted DiagnosticAttempt objects,
        }

    Raises ValueError for an invalid authoritative processing_run_id and
    RunIdentityError if a diagnostic attempt identifier cannot be created.
    """
    if parse_run_id(processing_run_id) is None:
        raise ValueError("processing_run_id must be a valid authoritative UUID string")
    attempts = _Attempts(processing_run_id, attempt_id_factory)

    texts = _document_texts(application)
    if texts is None or not isinstance(extraction, dict) or set(extraction) != set(EXTRACTION_KEYS):
        return _application_failure(attempts, processing_run_id)
    facts, reasons, evidence = (extraction[key] for key in EXTRACTION_KEYS)
    try:
        validate_candidate_facts(facts)
    except CandidateFactValidationError:
        return _application_failure(attempts, processing_run_id)
    if not isinstance(reasons, dict) or not isinstance(evidence, dict) or not set(evidence) <= set(DURATION_ROLES):
        return _application_failure(attempts, processing_run_id)

    candidate_facts = copy.deepcopy(facts)
    verified = {role: [] for role in DURATION_ROLES}
    errors = []

    for category, roles in FACT_SOURCES.items():
        for role in roles:
            record = facts[category][role]
            if category != DURATION_CATEGORY:
                # Only duration has extraction evidence; any other confident claim is untrusted.
                if record["status"] != "not_found":
                    attempts.add(role, category, "unsupported_fact_extraction", record)
                    candidate_facts[category][role] = _unprocessable()
                    errors.append(_error(role, category, processing_run_id))
                continue

            occurrences = evidence.get(role)
            rejected = False
            if not isinstance(occurrences, list):
                attempts.add(role, category, "malformed_occurrence", occurrences)
                rejected = True
                occurrences = []
            seen_spans = set()
            for occurrence in occurrences:
                problem = _occurrence_problem(occurrence, role, texts[role])
                if problem is None:
                    span = (occurrence["start_offset"], occurrence["end_offset"])
                    problem = "duplicate_occurrence" if span in seen_spans else None
                    seen_spans.add(span)
                if problem is not None:
                    attempts.add(role, category, problem, occurrence)
                    rejected = True
            if not rejected and not _record_supported(record, occurrences):
                attempts.add(role, category, "record_evidence_mismatch",
                             {"candidate_fact": record, "occurrences": occurrences})
                rejected = True

            if rejected:
                candidate_facts[category][role] = _unprocessable()
                errors.append(_error(role, category, processing_run_id))
            else:
                verified[role] = copy.deepcopy(occurrences)

    not_found_reasons = {}
    for category, roles in FACT_SOURCES.items():
        for role in roles:
            if candidate_facts[category][role]["status"] != "not_found":
                continue
            reason = reasons.get(category, {}).get(role) if isinstance(reasons.get(category), dict) else None
            if reason not in NOT_FOUND_REASON_CODES:
                return _application_failure(attempts, processing_run_id)
            not_found_reasons.setdefault(category, {})[role] = reason

    validate_candidate_facts(candidate_facts)
    if not validate_processing_errors(errors, processing_run_id)["valid"]:
        raise AssertionError("verifier produced invalid processing-error records")  # internal contract
    return {
        "application_integrity_failure": False,
        "candidate_facts": candidate_facts,
        "not_found_reasons": not_found_reasons,
        "verified_evidence": verified,
        "processing_errors": errors,
        "diagnostic_attempts": tuple(attempts.items),
    }
