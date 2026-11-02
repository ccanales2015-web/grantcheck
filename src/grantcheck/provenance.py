"""Deterministic provenance resolution for validated candidate facts.

For every candidate-fact source record, this module reports which ingested
document it came from (file name and SHA-256 of the original bytes) and exactly
where its evidence excerpt occurs in that document's stored text.

Locations use the text captured at document intake; source files are never
reread. Units:
    start_offset, end_offset  zero-based Unicode code-point indexes into the
                              decoded text; end_offset is exclusive, so
                              text[start_offset:end_offset] == evidence_text
    start_line, end_line      one-based line numbers counted by "\\n"; end_line
                              is the line holding the excerpt's final character

Matching is exact (case-, whitespace-, and line-ending-sensitive) and counts
overlapping occurrences. Only the first MAX_LOCATIONS locations are listed,
but occurrence_count is always exact.

The candidate-fact and rule-result structures are not changed; provenance is a
separate map keyed by the same fact categories and document roles.
"""

import hashlib

from grantcheck.fact_schema import FACT_SOURCES
from grantcheck.fact_validation import CandidateFactValidationError, validate_candidate_facts

MAX_LOCATIONS = 100

_REQUIRED_ROLES = tuple(dict.fromkeys(role for roles in FACT_SOURCES.values() for role in roles))


class ProvenanceError(ValueError):
    """The inputs do not satisfy the validated-fact contract needed for provenance."""


def find_occurrences(text, excerpt):
    """Find every exact, possibly overlapping occurrence of excerpt in text.

    Returns {"occurrence_count": int, "locations": [...]} with at most
    MAX_LOCATIONS locations in ascending offset order. Raises TypeError for
    non-string arguments and ValueError for an empty excerpt.
    """
    if not isinstance(text, str) or not isinstance(excerpt, str):
        raise TypeError("text and excerpt must both be strings")
    if not excerpt:
        raise ValueError("excerpt must not be empty")

    count = 0
    locations = []
    # Line numbers are tracked incrementally as offsets increase.
    line = 1
    counted_to = 0
    start = text.find(excerpt)
    while start != -1:
        count += 1
        if len(locations) < MAX_LOCATIONS:
            end = start + len(excerpt)
            line += text.count("\n", counted_to, start)
            counted_to = start
            locations.append({
                "start_offset": start,
                "end_offset": end,
                "start_line": line,
                "end_line": line + excerpt.count("\n", 0, len(excerpt) - 1),
            })
        start = text.find(excerpt, start + 1)  # step by one so overlaps are counted

    return {"occurrence_count": count, "locations": locations}


def _check_document(documents, role):
    """Return (filename, sha256, text) for one ingested document, or raise ProvenanceError."""
    document = documents.get(role)
    if not isinstance(document, dict):
        raise ProvenanceError(f"application documents[{role!r}] must be an ingested document record")

    filename = document.get("filename")
    text = document.get("text")
    sha256 = document.get("sha256")
    if not isinstance(filename, str) or not filename.strip():
        raise ProvenanceError(f"application documents[{role!r}]['filename'] must be a non-blank string")
    # Basename only: reject either separator, whatever the operating system.
    if "/" in filename or "\\" in filename:
        raise ProvenanceError(
            f"application documents[{role!r}]['filename'] must be a file name without directory components"
        )
    if not isinstance(text, str):
        raise ProvenanceError(f"application documents[{role!r}]['text'] must be a string")
    if not isinstance(sha256, str):
        raise ProvenanceError(f"application documents[{role!r}]['sha256'] must be a string")

    # Intake decodes strict UTF-8, so re-encoding reproduces the original bytes.
    # This ties the reported hash to the exact text the offsets refer to.
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha256:
        raise ProvenanceError(f"application documents[{role!r}]: sha256 does not match the stored text")

    return filename, sha256, text


def resolve_provenance(application, candidate_facts):
    """Build the provenance map for validated candidate facts.

    application is a document-intake record whose three documents were read;
    candidate_facts must satisfy the shared candidate-fact contract. Neither is
    modified. Returns a new dict mirroring FACT_SOURCES:

        {category: {role: {"filename", "document_sha256",
                           "occurrence_count", "locations"}}}

    Records with null evidence have occurrence_count 0 and no locations. Raises
    ProvenanceError if the inputs break the contract, including any non-null
    excerpt that does not occur in its document.
    """
    if not isinstance(application, dict):
        raise ProvenanceError("application must be a dict")
    documents = application.get("documents")
    if not isinstance(documents, dict):
        raise ProvenanceError("application documents must be a dict")
    sources = {role: _check_document(documents, role) for role in _REQUIRED_ROLES}

    try:
        validate_candidate_facts(candidate_facts)
    except CandidateFactValidationError as error:
        raise ProvenanceError(f"candidate facts are not valid: {error}") from error

    provenance = {}
    for category, roles in FACT_SOURCES.items():
        provenance[category] = {}
        for role in roles:
            filename, sha256, text = sources[role]
            evidence = candidate_facts[category][role]["evidence_text"]
            if evidence is None:
                found = {"occurrence_count": 0, "locations": []}
            else:
                found = find_occurrences(text, evidence)
                if found["occurrence_count"] == 0:
                    raise ProvenanceError(
                        f"candidate_facts.{category}.{role}.evidence_text: "
                        f"excerpt does not occur in the {role} document"
                    )
            provenance[category][role] = {
                "filename": filename,
                "document_sha256": sha256,
                "occurrence_count": found["occurrence_count"],
                "locations": found["locations"],
            }
    return provenance
