"""Deterministic, read-only evaluation of GrantCheck's four review rules.

Input contains normalized candidate facts, not raw grant-document text.
Findings flag matters for human review; they do not decide grant eligibility.
"""

from copy import deepcopy

from grantcheck.fact_validation import validate_candidate_facts

_RULES = (
    ("R01", "Personnel cost consistency", "personnel_cost_usd"),
    ("R02", "Personnel effort consistency", "personnel_effort_percent"),
)

_DURATION_RULE = ("R03", "Project duration consistency", "project_duration_months")

_ROLE_LABELS = {
    "budget": "Budget",
    "budget_justification": "Budget Justification",
    "project_narrative": "Project Narrative",
}


# Statuses whose null value must never be compared or treated as confirmed.
# "not_found" is not confirmed absence, so it is unresolved like "uncertain".
_UNRESOLVED = ("uncertain", "not_found")


def _outcome_for_consistency(records):
    statuses = [record["status"] for record in records.values()]
    if "unprocessable" in statuses or "missing" in statuses:
        return "not_evaluated"
    if any(status in _UNRESOLVED for status in statuses):
        return "uncertain"
    values = [record["value"] for record in records.values()]
    return "pass" if all(value == values[0] for value in values[1:]) else "finding"


def _months(value):
    return f"{value} month" if value == 1 else f"{value} months"


def _duration_outcome(records):
    """R03 outcome and explanation; a confident disagreement outranks unresolved inputs.

    Only validated "present" records count as confident evidence. Absent
    values are never treated as zero or compared.
    """
    present = [(role, record["value"]) for role, record in records.items() if record["status"] == "present"]
    unresolved = [(role, record["status"]) for role, record in records.items() if record["status"] != "present"]
    values = {value for _, value in present}

    confident = "; ".join(f"{_ROLE_LABELS[role]}: {_months(value)}" for role, value in present)
    limitations = ", ".join(f"{_ROLE_LABELS[role]} ({status})" for role, status in unresolved)

    if len(values) >= 2:
        message = f"Confident duration values disagree ({confident}); human review required."
        if unresolved:
            message += f" Also unresolved: {limitations}."
        return "finding", message

    if not unresolved:
        return "pass", f"All three confident duration values agree ({_months(present[0][1])})."

    if len(present) >= 2:
        agreed = " and ".join(_ROLE_LABELS[role] for role, _ in present)
        partial = f"{agreed} agree ({_months(present[0][1])})"
    elif present:
        partial = f"Only {_ROLE_LABELS[present[0][0]]} has a confident value ({_months(present[0][1])})"
    else:
        partial = "No confident duration values are available"

    if any(status in ("missing", "unprocessable") for _, status in unresolved):
        return "not_evaluated", (
            f"{partial}, but missing or unprocessable evidence prevents a complete comparison "
            f"and no confident disagreement was established. Unresolved: {limitations}."
        )
    return "uncertain", (
        f"{partial}, but unresolved evidence prevents a complete comparison and no confident "
        f"disagreement was established; human review required. Unresolved: {limitations}."
    )


def _travel_outcome(cost, justification):
    if cost["status"] in ("missing", "unprocessable"):
        return "not_evaluated"
    if cost["status"] in _UNRESOLVED:
        return "uncertain"
    if cost["value"] == 0:
        return "pass"  # A confirmed zero cost needs no justification.
    if justification["status"] == "unprocessable":
        return "not_evaluated"
    if justification["status"] == "missing":
        return "finding"
    if justification["status"] in _UNRESOLVED:
        return "uncertain"  # not_found never implies an adequate justification.
    return "pass"  # Validated present justification is exactly 'adequate'.


def _result(name, outcome, evidence, explanation=None):
    descriptions = {
        "pass": "No issue detected.",
        "finding": "Potential inconsistency or missing justification; human review required.",
        "uncertain": "Evidence is uncertain; human review required.",
        "not_evaluated": "Insufficient usable information to evaluate this rule.",
    }
    return {
        "rule_name": name,
        "outcome": outcome,
        "message": f"{name}: {explanation or descriptions[outcome]}",
        "evidence": deepcopy(evidence),
    }


def _duration_result(candidate_facts):
    _, name, category = _DURATION_RULE
    records = candidate_facts[category]
    outcome, explanation = _duration_outcome(records)
    return _result(name, outcome, {category: records}, explanation)


def evaluate_duration_rule(candidate_facts):
    """Validate once and return {"R03": result} only, for extraction mode.

    Uses exactly the same R03 evaluation as evaluate_rules(). Raises
    CandidateFactValidationError on invalid input. Never mutates input.
    """
    validate_candidate_facts(candidate_facts)
    return {_DURATION_RULE[0]: _duration_result(candidate_facts)}


def evaluate_rules(candidate_facts):
    """Validate once and return ordered R01–R04 results with detached evidence.

    Raises CandidateFactValidationError on invalid input. Never mutates input.
    """
    validate_candidate_facts(candidate_facts)
    results = {}
    for rule_id, name, category in _RULES:
        records = candidate_facts[category]
        results[rule_id] = _result(name, _outcome_for_consistency(records), {category: records})

    results[_DURATION_RULE[0]] = _duration_result(candidate_facts)

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
