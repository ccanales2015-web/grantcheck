"""Document intake: read the three source documents of one grant application.

Intake only answers "could each file be read as UTF-8 text?". It records the
exact text and a SHA-256 fingerprint of the original bytes for each document,
and collects a structured error for each document that could not be read.

It does not judge what the text says. Whitespace-only text, or a readable
placeholder such as S07's "[UNREADABLE DOCUMENT CONTENT]", passes intake;
deciding that content is unprocessable belongs to later extraction stages.
No AI extraction, consistency rules, persistence, or approval decisions here.
"""

import hashlib
from pathlib import Path

MAX_DOCUMENT_BYTES = 1_048_576  # 1 MiB per document

# Document roles, in the order they are processed and reported.
DOCUMENT_ROLES = ("budget", "budget_justification", "project_narrative")

# Reasons a single document can fail intake.
REASON_MISSING = "missing"
REASON_UNREADABLE = "unreadable"
REASON_FILE_TOO_LARGE = "file_too_large"
REASON_EMPTY = "empty"

INTAKE_COMPLETED = "completed"
INTAKE_EXCEPTION = "exception"
PROCESSING_PENDING = "pending"
PROCESSING_EXCEPTION = "exception"


class DocumentIntakeError(Exception):
    """One document could not be ingested; reason is one of the REASON_* values."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read_document(path):
    """Return {"filename", "text", "sha256"} for one file, or raise DocumentIntakeError."""
    path = Path(path)
    try:
        with path.open("rb") as file:
            # Read at most one byte past the limit, so an oversized file is
            # detected without loading all of it into memory.
            raw_bytes = file.read(MAX_DOCUMENT_BYTES + 1)
    except FileNotFoundError:
        raise DocumentIntakeError(REASON_MISSING) from None
    except OSError:
        # Directories, permission problems, and other I/O failures.
        raise DocumentIntakeError(REASON_UNREADABLE) from None

    if len(raw_bytes) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError(REASON_FILE_TOO_LARGE)
    if not raw_bytes:
        raise DocumentIntakeError(REASON_EMPTY)

    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise DocumentIntakeError(REASON_UNREADABLE) from None

    return {
        "filename": path.name,
        "text": text,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def intake_application(application_id, budget_path, justification_path, narrative_path):
    """Read an application's three documents and report what could be ingested.

    Raises ValueError if application_id is not a non-empty string; that is a
    caller mistake, not a document problem. Document problems never raise:
    each failed document is None in "documents" and has an entry in
    "intake_errors", while successfully read documents are kept.

    Returns a plain dict:
        {
            "application_id": "GC-S01",
            "documents": {"budget": {...} or None,
                          "budget_justification": {...} or None,
                          "project_narrative": {...} or None},
            "intake_errors": [{"document": role, "reason": reason}, ...],
            "intake_status": "completed" or "exception",
            "processing_status": "pending" or "exception",
        }
    """
    if not isinstance(application_id, str) or not application_id.strip():
        raise ValueError(f"application_id must be a non-empty string, got {application_id!r}")

    paths = {
        "budget": budget_path,
        "budget_justification": justification_path,
        "project_narrative": narrative_path,
    }

    documents = {}
    intake_errors = []
    for role in DOCUMENT_ROLES:
        try:
            documents[role] = _read_document(paths[role])
        except DocumentIntakeError as error:
            documents[role] = None
            intake_errors.append({"document": role, "reason": error.reason})

    if intake_errors:
        intake_status, processing_status = INTAKE_EXCEPTION, PROCESSING_EXCEPTION
    else:
        intake_status, processing_status = INTAKE_COMPLETED, PROCESSING_PENDING

    return {
        "application_id": application_id,
        "documents": documents,
        "intake_errors": intake_errors,
        "intake_status": intake_status,
        "processing_status": processing_status,
    }
