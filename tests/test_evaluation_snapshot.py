"""Tests for immutable initial evaluation snapshots (synthetic data only)."""
import copy
import dataclasses
import json
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.evaluation_snapshot import (  # noqa: E402
    INITIAL_EXTRACTION_EVALUATION,
    EvaluationSnapshot,
    create_initial_snapshot,
)
from grantcheck.fact_schema import FACT_SOURCES  # noqa: E402

RUN = "12345678-1234-4234-8234-123456789abc"


@pytest.fixture
def parts():
    facts = {c: {r: {"value": None, "status": "not_found", "evidence_text": None} for r in roles}
             for c, roles in FACT_SOURCES.items()}
    facts["project_duration_months"]["budget"] = {"value": 24, "status": "present",
                                                  "evidence_text": "Project Duration: 24 months"}
    evidence = {"budget": [{"document": "budget", "fact_category": "project_duration_months",
                            "excerpt": "Project Duration: 24 months", "start_offset": 0, "end_offset": 27,
                            "start_line": 1, "match": "valid", "value": 24}],
                "budget_justification": [], "project_narrative": []}
    result = {"rule_name": "Project duration consistency", "outcome": "uncertain",
              "message": "Project duration consistency: ...", "evidence": {"project_duration_months": facts["project_duration_months"]}}
    errors = [{"error_type": "extraction_failure", "document": "budget_justification",
               "fact_category": "project_duration_months", "processing_run_id": RUN}]
    return facts, evidence, result, errors


def make(parts):
    facts, evidence, result, errors = parts
    return create_initial_snapshot(RUN, facts, evidence, "R03", result, errors)


def test_snapshot_records_the_evaluation(parts):
    facts, evidence, result, errors = parts
    snapshot = make(parts)

    assert isinstance(snapshot, EvaluationSnapshot)
    assert snapshot.to_dict() == {
        "processing_run_id": RUN, "sequence": 0, "trigger": INITIAL_EXTRACTION_EVALUATION, "rule_id": "R03",
        "candidate_facts": facts, "verified_evidence": evidence, "rule_result": result, "processing_errors": errors,
    }
    assert INITIAL_EXTRACTION_EVALUATION == "initial_extraction_evaluation"


def test_snapshot_is_immutable(parts):
    snapshot = make(parts)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.sequence = 1
    assert isinstance(snapshot.candidate_facts, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.candidate_facts["project_duration_months"]["budget"]["value"] = 36
    with pytest.raises(AttributeError):
        snapshot.verified_evidence["budget"].append({})       # a tuple, not a list
    assert isinstance(snapshot.processing_errors, tuple)


def test_snapshot_is_detached_from_its_inputs(parts):
    snapshot = make(parts)
    before = snapshot.to_dict()
    facts, evidence, result, errors = parts

    facts["project_duration_months"]["budget"]["value"] = 99
    evidence["budget"].clear()
    result["outcome"] = "pass"
    errors.append({"error_type": "internal_processing_failure"})

    assert snapshot.to_dict() == before


def test_exported_copy_does_not_reach_the_snapshot(parts):
    snapshot = make(parts)
    exported = snapshot.to_dict()
    exported["candidate_facts"]["project_duration_months"]["budget"]["value"] = 99
    assert snapshot.to_dict()["candidate_facts"]["project_duration_months"]["budget"]["value"] == 24


def test_snapshot_needs_deliberate_serialization(parts):
    snapshot = make(parts)
    with pytest.raises(TypeError):
        json.dumps(snapshot)
    assert json.loads(json.dumps(snapshot.to_dict())) == snapshot.to_dict()
    assert "Project Duration" not in repr(snapshot)


@pytest.mark.parametrize("run", [None, "not-a-uuid", 5])
def test_snapshot_requires_a_valid_run_id(parts, run):
    facts, evidence, result, errors = parts
    with pytest.raises(ValueError):
        create_initial_snapshot(run, facts, evidence, "R03", result, errors)


def test_equal_inputs_give_equal_snapshots(parts):
    assert make(parts).to_dict() == make(copy.deepcopy(parts)).to_dict()
