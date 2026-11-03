"""Authoritative processing-run identifiers for extraction runs.

Each extraction run gets one processing_run_id, created by the orchestrator
before any document is processed. Identifiers are standard UUIDs written in the
canonical 8-4-4-4-12 hexadecimal form; capitalization does not matter when two
identifiers are compared, but the original strings are never rewritten.

This module does not touch the replay workflow, whose results carry no run ID.
"""

import re
import uuid

_CANONICAL_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class RunIdentityError(Exception):
    """An authoritative processing-run identifier could not be initialized."""


def new_processing_run_id(uuid_factory=uuid.uuid4):
    """Create the authoritative run ID as a lowercase canonical UUID string.

    uuid_factory is injectable for deterministic tests and must return a
    uuid.UUID. Any failure raises RunIdentityError, so the caller can stop
    before document processing; a replacement ID is never invented.
    """
    try:
        value = uuid_factory()
    except Exception as error:  # noqa: BLE001 - any provider failure stops the run
        raise RunIdentityError("processing run identifier could not be initialized") from error
    if not isinstance(value, uuid.UUID):
        raise RunIdentityError("processing run identifier could not be initialized")
    return str(value)


def parse_run_id(value):
    """Return the uuid.UUID for a canonical UUID string, or None if it is not one.

    Accepts upper-, lower-, or mixed-case hexadecimal. Braces, "urn:uuid:"
    prefixes, missing hyphens, and non-string values are rejected.
    """
    if not isinstance(value, str) or not _CANONICAL_UUID.match(value):
        return None
    return uuid.UUID(value)


def same_run(first, second):
    """True if both are valid run IDs naming the same UUID, ignoring capitalization."""
    first_uuid, second_uuid = parse_run_id(first), parse_run_id(second)
    return first_uuid is not None and first_uuid == second_uuid
