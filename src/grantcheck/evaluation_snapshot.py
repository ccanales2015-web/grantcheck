"""Immutable evaluation snapshots for extraction-mode rule evaluation.

A snapshot records exactly what one evaluation saw and concluded: the
authoritative processing-run UUID, the event that triggered it, the 12
candidate-fact records, the verified duration evidence with its source
locations, the R03 result, and the processing-error records. It is built from
deep copies, so later changes to the application, the extraction output, or
the response cannot alter it.

Snapshots carry a sequence number so that later reevaluations can be appended
as new snapshots rather than overwriting this one; reevaluation itself is not
implemented yet. A snapshot cannot be serialized by accident (json.dumps
raises TypeError); use to_dict() for a deliberate, JSON-compatible export.
"""

import copy
from dataclasses import dataclass

from grantcheck.immutable import deep_freeze, deep_thaw
from grantcheck.run_identity import parse_run_id

INITIAL_EXTRACTION_EVALUATION = "initial_extraction_evaluation"


@dataclass(frozen=True, repr=False)
class EvaluationSnapshot:
    """One immutable, append-only entry in an application's evaluation history."""

    processing_run_id: str
    sequence: int
    trigger: str
    rule_id: str
    candidate_facts: object       # frozen 12-record candidate facts
    verified_evidence: object     # frozen {role: (occurrence, ...)}
    rule_result: object           # frozen four-field rule result
    processing_errors: object     # frozen tuple of processing-error records

    def __repr__(self):
        return (f"EvaluationSnapshot(processing_run_id={self.processing_run_id!r}, "
                f"sequence={self.sequence}, trigger={self.trigger!r}, rule_id={self.rule_id!r})")

    def to_dict(self):
        """Plain, JSON-compatible copy for a deliberate export."""
        return {
            "processing_run_id": self.processing_run_id,
            "sequence": self.sequence,
            "trigger": self.trigger,
            "rule_id": self.rule_id,
            "candidate_facts": deep_thaw(self.candidate_facts),
            "verified_evidence": deep_thaw(self.verified_evidence),
            "rule_result": deep_thaw(self.rule_result),
            "processing_errors": deep_thaw(self.processing_errors),
        }


def create_initial_snapshot(processing_run_id, candidate_facts, verified_evidence, rule_id, rule_result,
                            processing_errors):
    """The first snapshot (sequence 0) of a completed extraction evaluation.

    All inputs are deep-copied before freezing. Raises ValueError if
    processing_run_id is not a valid UUID string.
    """
    if parse_run_id(processing_run_id) is None:
        raise ValueError("processing_run_id must be a valid authoritative UUID string")
    detached = copy.deepcopy((candidate_facts, verified_evidence, rule_result, list(processing_errors)))
    facts, evidence, result, errors = (deep_freeze(part) for part in detached)
    return EvaluationSnapshot(
        processing_run_id=processing_run_id,
        sequence=0,
        trigger=INITIAL_EXTRACTION_EVALUATION,
        rule_id=rule_id,
        candidate_facts=facts,
        verified_evidence=evidence,
        rule_result=result,
        processing_errors=errors,
    )
