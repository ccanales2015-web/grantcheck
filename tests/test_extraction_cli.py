
"""End-to-end tests for GrantCheck's extraction CLI."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"


def test_extraction_cli_help():
    """The extraction CLI exposes its required arguments."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grantcheck.extraction_cli",
            "--help",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "--application-id" in result.stdout
    assert "--budget" in result.stdout
    assert "--justification" in result.stdout
    assert "--narrative" in result.stdout


def test_extraction_cli_detects_duration_conflict(tmp_path):
    """The extraction CLI identifies conflicting durations without replay facts."""
    documents = {
        "budget.txt": "Project Duration: 24 months",
        "budget_justification.txt": "Project Duration: 36 months",
        "project_narrative.txt": "Project Duration: 24 months",
    }

    for filename, content in documents.items():
        (tmp_path / filename).write_text(content, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grantcheck.extraction_cli",
            "--application-id",
            "GC-TEST-CONFLICT",
            "--budget",
            str(tmp_path / "budget.txt"),
            "--justification",
            str(tmp_path / "budget_justification.txt"),
            "--narrative",
            str(tmp_path / "project_narrative.txt"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Application ID: GC-TEST-CONFLICT" in result.stdout
    assert "Processing status: completed" in result.stdout
    assert "Human review required: yes" in result.stdout
    assert "R03: finding" in result.stdout
    assert "does not approve, reject, or determine grant eligibility" in result.stdout


def test_extraction_cli_handles_missing_document(tmp_path):
    """A missing document produces a sanitized failure report."""
    justification = tmp_path / "budget_justification.txt"
    narrative = tmp_path / "project_narrative.txt"

    justification.write_text("Project Duration: 24 months", encoding="utf-8")
    narrative.write_text("Project Duration: 24 months", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grantcheck.extraction_cli",
            "--application-id",
            "GC-TEST-MISSING",
            "--budget",
            str(tmp_path / "missing_budget.txt"),
            "--justification",
            str(justification),
            "--narrative",
            str(narrative),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Processing status: exception" in result.stdout
    assert "Failure code: application_integrity_failure" in result.stdout
    assert "Rule outcomes: not available" in result.stdout
    assert "Human review required: processing could not be completed" in result.stdout
    assert "Traceback" not in result.stdout
    assert "Project Duration: 24 months" not in result.stdout
    assert "does not approve, reject, or determine grant eligibility" in result.stdout


def test_extraction_cli_does_not_expose_internal_data(tmp_path):
    """CLI output excludes source text and restricted internal structures."""
    documents = {
        "budget.txt": "Project Duration: 24 months\nPRIVATE-BUDGET-CANARY-123",
        "budget_justification.txt": "Project Duration: 36 months\nPRIVATE-JUSTIFICATION-CANARY-456",
        "project_narrative.txt": "Project Duration: 24 months\nPRIVATE-NARRATIVE-CANARY-789",
    }

    for filename, content in documents.items():
        (tmp_path / filename).write_text(content, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grantcheck.extraction_cli",
            "--application-id",
            "GC-TEST-PRIVACY",
            "--budget",
            str(tmp_path / "budget.txt"),
            "--justification",
            str(tmp_path / "budget_justification.txt"),
            "--narrative",
            str(tmp_path / "project_narrative.txt"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "R03: finding" in result.stdout

    for forbidden in (
        "PRIVATE-BUDGET-CANARY-123",
        "PRIVATE-JUSTIFICATION-CANARY-456",
        "PRIVATE-NARRATIVE-CANARY-789",
        "Project Duration: 24 months",
        "Project Duration: 36 months",
        "diagnostic_attempts",
        "snapshot",
        "verified_evidence",
        "candidate_facts",
    ):
        assert forbidden not in result.stdout
        assert forbidden not in result.stderr


def test_extraction_cli_rejects_missing_arguments():
    """Missing document paths produce a usage error without a traceback."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grantcheck.extraction_cli",
            "--application-id",
            "GC-TEST",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "--budget" in result.stderr
    assert "--justification" in result.stderr
    assert "--narrative" in result.stderr
    assert "Traceback" not in result.stderr
    assert "does not approve, reject, or determine grant eligibility" in result.stderr
