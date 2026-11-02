"""Deterministic, read-only evaluation of GrantCheck's four review rules.

Input contains normalized candidate facts, not raw grant-document text.
Findings flag matters for human review; they do not decide grant eligibility.
"""

from copy import deepcopy

from grantcheck.fact_validation import validate_candidate_facts

_RULES = (
    ("R01", "Personnel cost consistency", "personnel_cost_usd"),
    ("R02", "Personnel effort consistency", "personnel_effort_percent"),
    ("R03", "Project duration consistency", "project_duration_months"),
)


def _outcome_for_consistency(records):
    statuses = [record["status"] for record in records.values()]
    if "unprocessable" in statuses or "missing" in statuses:
        return "not_evaluated"
    if "uncertain" in statuses:
        return "uncertain"
    values = [record["value"] for record in records.values()]
    return "pass" if all(value == values[0] for value in values[1:]) else "finding"


def _travel_outcome(cost, justification):
    if cost["status"] in ("missing", "unprocessable"):
        return "not_evaluated"
    if cost["status"] == "uncertain":
        return "uncertain"
    if cost["value"] == 0:
        return "pass"
    if justification["status"] == "unprocessable":
        return "not_evaluated"
    if justification["status"] == "missing":
        return "finding"
    if justification["status"] == "uncertain":
        return "uncertain"
    return "pass"  # Validated present justification is exactly 'adequate'.


def _result(name, outcome, evidence):
    descriptions = {
        "pass": "No issue detected.",
        "finding": "Potential inconsistency or missing justification; human review required.",
        "uncertain": "Evidence is uncertain; human review required.",
        "not_evaluated": "Insufficient usable information to evaluate this rule.",
    }
    return {
        "rule_name": name,
        "outcome": outcome,
        "message": f"{name}: {descriptions[outcome]}",
        "evidence": deepcopy(evidence),
    }


def evaluate_rules(candidate_facts):
    """Validate once and return ordered R01–R04 results with detached evidence.

    Raises CandidateFactValidationError on invalid input. Never mutates input.
    """
    validate_candidate_facts(candidate_facts)
    results = {}
    for rule_id, name, category in _RULES:
        records = candidate_facts[category]
        results[rule_id] = _result(name, _outcome_for_consistency(records), {category: records})

    cost = candidate_facts["travel_cost_usd"]["budget"]
    justification = candidate_facts["travel_justification"]["budget_justification"]
    results["R04"] = _result(
        "Travel justification required for positive budget travel cost",
        _travel_outcome(cost, justification),
        {
            "travel_cost_usd": {"budget": cost},
            "travel_justification": {"budget_justification": justification},
        },
    )
    return results
