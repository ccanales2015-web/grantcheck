"""Offline replay loader: read pre-recorded candidate facts for one application.

A replay file (replay_facts.json) stands in for live AI extraction. It holds
the candidate facts an extractor would have produced, each with the verbatim
evidence it was read from. This module checks that a replay file is safe to
parse, follows the authoritative schema in grantcheck.fact_schema, belongs to
the given application, and quotes evidence that really occurs in the text
stored at intake.

It validates structure and provenance only. It does not judge whether an
interpretation is correct (e.g. "two years" -> 24), evaluate cross-document
rules, call an AI service, write files, or change the application record.

Errors:
    ValueError             the application record does not meet the prerequisites
    ReplayValidationError  the replay file is missing, unreadable, or invalid
    TypeError              replay_path is not a usable path type (caller mistake)
"""

import json
import math
from pathlib import Path

from grantcheck.document_intake import DOCUMENT_ROLES, INTAKE_COMPLETED
from grantcheck.fact_schema import (
    FACT_ALLOWED_VALUES,
    FACT_SOURCES,
    FACT_STATUSES,
    FACT_VALUE_TYPES,
    SOURCE_RECORD_FIELDS,
)

MAX_REPLAY_BYTES = 1_048_576  # 1 MiB per replay file

TOP_LEVEL_KEYS = ("application_id", "candidate_facts")

_BYTE_ORDER_MARK = "\ufeff"
_TOP_LEVEL_FIELD = "(top level)"
_PREVIEW_LENGTH = 80


class ReplayValidationError(ValueError):
    """The replay file is missing, unreadable, or breaks the approved replay schema."""


# --- Small helpers -----------------------------------------------------------


def _is_nonblank_string(value):
    return isinstance(value, str) and bool(value.strip())


def _json_type(value):
    """Name a parsed value by its JSON type, for error messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _preview(value):
    """A short repr of a value, so a huge string cannot flood an error message."""
    text = repr(value)
    if len(text) > _PREVIEW_LENGTH:
        text = text[:_PREVIEW_LENGTH] + "..."
    return text


def _describe_type(type_spec):
    if type_spec is int:
        return "an integer"
    if type_spec is str:
        return "a string"
    return "a number"


def _location(path):
    """"<scenario folder>/<file name>", or just the file name if there is no parent folder."""
    parent = path.parent.name
    return f"{parent}/{path.name}" if parent else path.name


def _fail(where, field, reason):
    raise ReplayValidationError(f"{where}: {field}: {reason}")


def _require_object(value, where, field):
    if not isinstance(value, dict):
        _fail(where, field, f"must be a JSON object, got {_json_type(value)}")


def _check_keys(container, expected_keys, where, field):
    """Require the exact key set; report every missing and unexpected key."""
    expected = tuple(expected_keys)
    if set(container) == set(expected):
        return
    missing = [key for key in expected if key not in container]
    unexpected = [key for key in container if key not in expected]
    problems = []
    if missing:
        problems.append(f"missing keys {missing}")
    if unexpected:
        problems.append(f"unexpected keys {unexpected}")
    _fail(where, field, "; ".join(problems))


# --- Stage 1: application prerequisites (plain ValueError) -------------------


def _check_application(application):
    """Return (application_id, documents) or raise ValueError."""
    if not isinstance(application, dict):
        raise ValueError(f"application must be a dict, got {type(application).__name__}")

    application_id = application.get("application_id")
    if not _is_nonblank_string(application_id):
        raise ValueError(f"application_id must be a non-blank string, got {_preview(application_id)}")

    intake_status = application.get("intake_status")
    if intake_status != INTAKE_COMPLETED:
        raise ValueError(
            f"application {application_id!r}: intake_status must be {INTAKE_COMPLETED!r}, "
            f"got {_preview(intake_status)}"
        )

    documents = application.get("documents")
    if not isinstance(documents, dict):
        raise ValueError(f"application {application_id!r}: documents must be a dict")

    for role in DOCUMENT_ROLES:
        if role not in documents:
            raise ValueError(f"application {application_id!r}: documents is missing {role!r}")
        record = documents[role]
        if not isinstance(record, dict):
            raise ValueError(f"application {application_id!r}: documents[{role!r}] must be a dict")
        text = record.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError(
                f"application {application_id!r}: documents[{role!r}]['text'] must be a non-empty string"
            )

    return application_id, documents


# --- Stages 2-3: bounded reading, UTF-8, strict JSON -------------------------


def _read_replay_text(path, where):
    try:
        with path.open("rb") as file:
            # Read one byte past the limit so an oversized file is detected
            # without loading all of it into memory.
            raw_bytes = file.read(MAX_REPLAY_BYTES + 1)
    except FileNotFoundError:
        raise ReplayValidationError(f"{where}: replay file is missing") from None
    except OSError as error:
        # Directories, permission problems, and other paths that cannot be read as files.
        raise ReplayValidationError(
            f"{where}: replay file could not be read ({type(error).__name__})"
        ) from None

    if len(raw_bytes) > MAX_REPLAY_BYTES:
        raise ReplayValidationError(f"{where}: replay file exceeds {MAX_REPLAY_BYTES:,} bytes")

    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ReplayValidationError(
            f"{where}: replay file is not valid UTF-8 (invalid byte at offset {error.start})"
        ) from None

    if text.startswith(_BYTE_ORDER_MARK):
        raise ReplayValidationError(f"{where}: replay file must not start with a UTF-8 byte order mark")

    return text


def _parse_json(text, where):
    def reject_duplicate_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ReplayValidationError(f"{where}: duplicate JSON key {key!r}")
            obj[key] = value
        return obj  # plain dict keeps the original key order

    def reject_constant(name):
        raise ReplayValidationError(f"{where}: non-standard JSON constant {name} is not allowed")

    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_constant)
    except ReplayValidationError:
        # Raised by the hooks above; already in the right form. Must precede the
        # ValueError handlers, since ReplayValidationError is a ValueError.
        raise
    except json.JSONDecodeError as error:
        raise ReplayValidationError(
            f"{where}: malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from None
    except ValueError as error:
        # e.g. an integer literal longer than Python's configured digit limit.
        raise ReplayValidationError(f"{where}: JSON value could not be parsed ({error})") from None
    except RecursionError:
        raise ReplayValidationError(f"{where}: JSON is nested too deeply") from None


# --- Stage 8: source records -------------------------------------------------


def _check_value(fact, value, status, where, field):
    if status != "present":
        if value is not None:
            _fail(where, field, f"must be null when status is {status!r}, got {_preview(value)}")
        return

    if value is None:
        _fail(where, field, "must not be null when status is 'present'")

    expected_type = FACT_VALUE_TYPES[fact]
    # bool is a subclass of int, so it must be excluded explicitly.
    if isinstance(value, bool) or not isinstance(value, expected_type):
        _fail(where, field,
              f"must be {_describe_type(expected_type)}, got {_json_type(value)} {_preview(value)}")
    # Only floats can be non-finite; converting a huge int to float could overflow.
    if isinstance(value, float) and not math.isfinite(value):
        _fail(where, field, f"must be a finite number, got {value!r}")
    if isinstance(value, str) and not value.strip():
        _fail(where, field, "must be a non-blank string")

    allowed_values = FACT_ALLOWED_VALUES.get(fact)
    if allowed_values is not None and value not in allowed_values:
        _fail(where, field, f"{_preview(value)} is not one of {list(allowed_values)}")


def _check_evidence(evidence, status, role, document_text, where, field):
    if evidence is None:
        if status == "present":
            _fail(where, field, "must be a non-blank string when status is 'present', got null")
        return

    if not _is_nonblank_string(evidence):
        _fail(where, field, f"must be null or a non-blank string, got {_json_type(evidence)} {_preview(evidence)}")

    # Exact, case-sensitive match against the intake snapshot: no trimming and
    # no LF/CRLF normalization. One occurrence anywhere is enough.
    if evidence not in document_text:
        _fail(where, field, f"{_preview(evidence)} does not occur verbatim in the stored {role} text")


def _check_record(fact, role, record, document_text, where, field):
    _require_object(record, where, field)
    _check_keys(record, SOURCE_RECORD_FIELDS, where, field)

    status = record["status"]
    if not isinstance(status, str) or status not in FACT_STATUSES:
        _fail(where, f"{field}.status", f"{_preview(status)} is not one of {list(FACT_STATUSES)}")

    _check_value(fact, record["value"], status, where, f"{field}.value")
    _check_evidence(record["evidence_text"], status, role, document_text, where, f"{field}.evidence_text")


# --- Public entry point ------------------------------------------------------


def load_replay_facts(application, replay_path):
    """Load, validate, and return the candidate_facts of a replay file.

    application is an intake record from grantcheck.document_intake whose
    intake completed; it is only read, never modified. replay_path is a
    filesystem path to the replay JSON file.

    Validation stops at the first problem. On success, returns the parsed
    candidate_facts dict itself (original key order, no copy).
    """
    application_id, documents = _check_application(application)

    path = Path(replay_path)  # TypeError for unsupported argument types
    where = _location(path)
    replay = _parse_json(_read_replay_text(path, where), where)

    _require_object(replay, where, _TOP_LEVEL_FIELD)
    _check_keys(replay, TOP_LEVEL_KEYS, where, _TOP_LEVEL_FIELD)

    replay_id = replay["application_id"]
    if not _is_nonblank_string(replay_id):
        _fail(where, "application_id",
              f"must be a non-blank string, got {_json_type(replay_id)} {_preview(replay_id)}")
    if replay_id != application_id:
        _fail(where, "application_id",
              f"{replay_id!r} does not match the application's ID {application_id!r}")

    candidate_facts = replay["candidate_facts"]
    _require_object(candidate_facts, where, "candidate_facts")
    _check_keys(candidate_facts, FACT_SOURCES, where, "candidate_facts")

    # Validate every category container before any individual record, in schema order.
    for fact, roles in FACT_SOURCES.items():
        field = f"candidate_facts.{fact}"
        _require_object(candidate_facts[fact], where, field)
        _check_keys(candidate_facts[fact], roles, where, field)

    for fact, roles in FACT_SOURCES.items():
        for role in roles:
            _check_record(
                fact,
                role,
                candidate_facts[fact][role],
                documents[role]["text"],
                where,
                f"candidate_facts.{fact}.{role}",
            )

    return candidate_facts
