"""Tests for authoritative processing-run identifiers (synthetic UUIDs only)."""
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.run_identity import RunIdentityError, new_processing_run_id, parse_run_id, same_run  # noqa: E402

RUN = "12345678-1234-4234-8234-123456789abc"


def test_default_generates_a_standard_uuid():
    run_id = new_processing_run_id()
    assert parse_run_id(run_id) is not None
    assert run_id == run_id.lower()
    assert uuid.UUID(run_id).version == 4


def test_generated_ids_differ_between_runs():
    assert new_processing_run_id() != new_processing_run_id()


def test_uuid_provider_is_injectable():
    assert new_processing_run_id(lambda: uuid.UUID(RUN)) == RUN


@pytest.mark.parametrize("provider", [
    lambda: (_ for _ in ()).throw(OSError("entropy unavailable")),
    lambda: RUN,              # a string, not a uuid.UUID
    lambda: None,
    lambda: 1234,
], ids=["raises", "returns_string", "returns_none", "returns_int"])
def test_initialization_failure_raises_without_inventing_an_id(provider):
    with pytest.raises(RunIdentityError) as error:
        new_processing_run_id(provider)
    assert str(error.value) == "processing run identifier could not be initialized"
    assert "entropy" not in str(error.value)


@pytest.mark.parametrize("text", [RUN, RUN.upper(), "12345678-1234-4234-8234-123456789ABC"])
def test_parse_accepts_canonical_uuid_in_any_case(text):
    assert parse_run_id(text) == uuid.UUID(RUN)


@pytest.mark.parametrize("text", [
    "", "not-a-uuid", RUN[:-1], RUN + "0", RUN.replace("-", ""), "{" + RUN + "}",
    "urn:uuid:" + RUN, " " + RUN, "12345678-1234-4234-8234-123456789abg",
    None, 123, uuid.UUID(RUN), b"12345678-1234-4234-8234-123456789abc",
], ids=repr)
def test_parse_rejects_non_canonical_or_non_string(text):
    assert parse_run_id(text) is None


def test_same_run_ignores_capitalization():
    assert same_run(RUN, RUN.upper())
    assert not same_run(RUN, "87654321-4321-4321-8321-cba987654321")
    assert not same_run(RUN, "not-a-uuid")
    assert not same_run(None, None)


def test_parsing_does_not_rewrite_the_original_string():
    original = RUN.upper()
    parse_run_id(original)
    same_run(original, RUN)
    assert original == RUN.upper()
