"""Deterministic, offline extraction of project duration from structured statements.

Reads the three documents of a document-intake record and produces the full
set of 12 candidate-fact records. Only project_duration_months is extracted;
every other fact category is reported as not_found.

A duration statement is a line that begins (after optional spaces or tabs)
with the exact, case-sensitive label "Project Duration:" or "Duration:".
Longer labels such as "Equipment Warranty Duration:" and labels inside
narrative sentences are not statements. A confident value is a positive
integer followed by a month or year unit, for example "24 months" or
"2 years" (years are normalized to months). Anything else after a recognized
label is ambiguous and never becomes a confident value.

Per document:
    no statement                         -> not_found (no_supported_pattern_matched)
    all statements valid, one value      -> present
    valid statements with different values, or any ambiguous statement
                                         -> uncertain

No rules are evaluated, no files are read, and the inputs are not modified.
"""

import re

from grantcheck.fact_schema import FACT_SOURCES, NOT_FOUND_REASON_CODES
from grantcheck.fact_validation import validate_candidate_facts

DURATION_CATEGORY = "project_duration_months"
DURATION_LABELS = ("Project Duration:", "Duration:")

UNSUPPORTED_FACT_TYPE, NO_SUPPORTED_PATTERN_MATCHED = NOT_FOUND_REASON_CODES

MATCH_VALID = "valid"
MATCH_AMBIGUOUS = "ambiguous"

# A label at the start of a line; [^\r\n]* keeps CRLF and LF line endings out of the remainder.
_STATEMENT = re.compile(r"^[ \t]*(Project Duration:|Duration:)([^\r\n]*)", re.MULTILINE)
# A positive integer (ASCII digits, no leading zero) and a month or year unit; an optional final period.
_QUANTITY = re.compile(r"([1-9][0-9]*)[ \t]+(months?|years?)\.?", re.IGNORECASE)

_NOT_FOUND = {"value": None, "status": "not_found", "evidence_text": None}


def normalize_duration_quantity(remainder):
    """Normalize the text after a duration label to whole months.

    remainder is everything following "Project Duration:" or "Duration:" on
    the statement's line. Surrounding spaces and tabs are ignored. Returns a
    positive int (years multiplied by 12) for a confident quantity such as
    "24 months" or "2 years", otherwise None. This is the single
    normalization rule shared by extraction and provenance verification.
    """
    match = _QUANTITY.fullmatch(remainder.strip(" \t"))
    if match is None:
        return None
    quantity = int(match.group(1))
    return quantity * 12 if match.group(2).lower().startswith("year") else quantity


def _occurrences(role, text):
    """Every duration statement in one document, in offset order."""
    found = []
    for match in _STATEMENT.finditer(text):
        start = match.start(1)
        remainder = match.group(2)
        end = match.start(2) + len(remainder.rstrip(" \t"))
        months = normalize_duration_quantity(remainder)
        found.append({
            "document": role,
            "fact_category": DURATION_CATEGORY,
            "excerpt": text[start:end],
            "start_offset": start,
            "end_offset": end,
            "start_line": text.count("\n", 0, start) + 1,
            "match": MATCH_VALID if months is not None else MATCH_AMBIGUOUS,
            "value": months,
        })
    return found


def _duration_record(occurrences):
    """The candidate-fact record for one document's duration statements."""
    if not occurrences:
        return dict(_NOT_FOUND)
    ambiguous = [occurrence for occurrence in occurrences if occurrence["match"] == MATCH_AMBIGUOUS]
    values = {occurrence["value"] for occurrence in occurrences if occurrence["match"] == MATCH_VALID}
    if ambiguous:
        return {"value": None, "status": "uncertain", "evidence_text": ambiguous[0]["excerpt"]}
    if len(values) > 1:
        return {"value": None, "status": "uncertain", "evidence_text": occurrences[0]["excerpt"]}
    return {"value": values.pop(), "status": "present", "evidence_text": occurrences[0]["excerpt"]}


def _document_texts(application):
    """Stored intake text for each role the schema reads from, or ValueError."""
    if not isinstance(application, dict) or not isinstance(application.get("documents"), dict):
        raise ValueError("application must be a document-intake record with a documents dict")
    roles = dict.fromkeys(role for roles in FACT_SOURCES.values() for role in roles)
    texts = {}
    for role in roles:
        document = application["documents"].get(role)
        if not isinstance(document, dict) or not isinstance(document.get("text"), str):
            raise ValueError(f"application documents[{role!r}] must be an ingested document with text")
        texts[role] = document["text"]
    return texts


def extract_candidate_facts(application):
    """Extract candidate facts from a document-intake record.

    Returns a new dict:
        {
            "candidate_facts":   the 12 records, in FACT_SOURCES order, each
                                 {"value", "status", "evidence_text"},
            "not_found_reasons": {category: {role: reason}} for every
                                 not_found record only, using
                                 NOT_FOUND_REASON_CODES,
            "duration_evidence": {role: [occurrence, ...]} for the three
                                 duration documents (empty lists allowed),
        }

    Each occurrence is {"document", "fact_category", "excerpt",
    "start_offset", "end_offset", "start_line", "match", "value"}: offsets
    are zero-based code-point indexes into the intake text with an exclusive
    end, start_line is one-based, match is "valid" or "ambiguous", and value
    is normalized months for a valid match, otherwise None.

    Raises ValueError if the application lacks a required document text.
    """
    texts = _document_texts(application)

    candidate_facts = {}
    reasons = {}
    evidence = {}
    for category, roles in FACT_SOURCES.items():
        candidate_facts[category] = {}
        for role in roles:
            if category == DURATION_CATEGORY:
                occurrences = _occurrences(role, texts[role])
                evidence[role] = occurrences
                record = _duration_record(occurrences)
                reason = NO_SUPPORTED_PATTERN_MATCHED
            else:
                record = dict(_NOT_FOUND)
                reason = UNSUPPORTED_FACT_TYPE
            candidate_facts[category][role] = record
            if record["status"] == "not_found":
                reasons.setdefault(category, {})[role] = reason

    validate_candidate_facts(candidate_facts)  # extraction output must meet the shared contract
    return {"candidate_facts": candidate_facts, "not_found_reasons": reasons, "duration_evidence": evidence}
