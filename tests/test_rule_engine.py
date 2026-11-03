"""Offline, deterministic tests for the four GrantCheck review rules."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.fact_validation import CandidateFactValidationError  # noqa: E402
from grantcheck.rule_engine import evaluate_rules  # noqa: E402


@pytest.fixture
def facts():
    return json.loads((ROOT / "sample_data" / "S01_consistent" / "replay_facts.json").read_text(encoding="utf-8"))["candidate_facts"]


def change(facts, category, role, *, status="present", value=None):
    record = facts[category][role]
    record["status"] = status
    if status == "present":
        record["value"] = value
        record["evidence_text"] = "Test excerpt"
    else:
        record["value"] = None
        record["evidence_text"] = "Test excerpt" if status == "uncertain" else None


def test_baseline_all_pass(facts):
    results = evaluate_rules(facts)
    assert list(results) == ["R01", "R02", "R03", "R04"]
    assert [result["outcome"] for result in results.values()] == ["pass"] * 4
    assert all(set(result) == {"rule_name", "outcome", "message", "evidence"} for result in results.values())


@pytest.mark.parametrize("rule,category,role,value", [
    ("R01", "personnel_cost_usd", "budget_justification", 20000),
    ("R02", "personnel_effort_percent", "budget_justification", 25),
    ("R03", "project_duration_months", "project_narrative", 36),
])
def test_mismatches(facts, rule, category, role, value):
    change(facts, category, role, value=value)
    assert evaluate_rules(facts)[rule]["outcome"] == "finding"


@pytest.mark.parametrize("status,expected", [
    ("missing", "not_evaluated"), ("unprocessable", "not_evaluated"),
    ("uncertain", "uncertain"),
])
def test_consistency_status(facts, status, expected):
    change(facts, "personnel_cost_usd", "budget_justification", status=status)
    assert evaluate_rules(facts)["R01"]["outcome"] == expected


def test_missing_overrides_uncertain(facts):
    change(facts, "project_duration_months", "budget", status="uncertain")
    change(facts, "project_duration_months", "project_narrative", status="missing")
    assert evaluate_rules(facts)["R03"]["outcome"] == "not_evaluated"


@pytest.mark.parametrize("cost_status,cost_value,just_status,expected", [
    ("present", 0, "missing", "pass"),
    ("present", 8000, "missing", "finding"),
    ("present", 8000, "uncertain", "uncertain"),
    ("present", 8000, "unprocessable", "not_evaluated"),
    ("present", 8000, "present", "pass"),
    ("missing", None, "present", "not_evaluated"),
    ("uncertain", None, "present", "uncertain"),
    ("unprocessable", None, "present", "not_evaluated"),
])
def test_travel_outcomes(facts, cost_status, cost_value, just_status, expected):
    change(facts, "travel_cost_usd", "budget", status=cost_status, value=cost_value)
    change(facts, "travel_justification", "budget_justification", status=just_status,
           value="adequate" if just_status == "present" else None)
    assert evaluate_rules(facts)["R04"]["outcome"] == expected


def test_does_not_mutate_and_evidence_is_detached(facts):
    before = copy.deepcopy(facts)
    result = evaluate_rules(facts)
    assert facts == before
    result["R01"]["evidence"]["personnel_cost_usd"]["budget"]["value"] = 0
    assert facts == before


def test_invalid_input_is_atomic(facts):
    facts["project_duration_months"]["budget"]["value"] = 0
    with pytest.raises(CandidateFactValidationError):
        evaluate_rules(facts)


@pytest.mark.parametrize("scenario_prefix,expected", [
    ("S01_", ("pass", "pass", "pass", "pass")),
    ("S02_", ("finding", "pass", "pass", "pass")),
    ("S03_", ("pass", "finding", "pass", "pass")),
    ("S04_", ("pass", "pass", "finding", "pass")),
    ("S05_", ("pass", "pass", "pass", "finding")),
    ("S06_", ("pass", "pass", "pass", "uncertain")),
    ("S07_", ("not_evaluated", "not_evaluated", "not_evaluated", "not_evaluated")),
])
def test_all_scenario_replays(scenario_prefix, expected):
    matches = list((ROOT / "sample_data").glob(f"{scenario_prefix}*/replay_facts.json"))
    assert len(matches) == 1, f"Expected one replay fixture for {scenario_prefix}"
    facts = json.loads(matches[0].read_text(encoding="utf-8"))["candidate_facts"]
    results = evaluate_rules(facts)
    assert tuple(result["outcome"] for result in results.values()) == expected


# --- not_found safeguards (Increment 1A) ----------------------------------------

CONSISTENCY_RULES = [
    ("R01", "personnel_cost_usd", ("budget", "budget_justification")),
    ("R02", "personnel_effort_percent", ("budget", "budget_justification")),
    ("R03", "project_duration_months", ("budget", "budget_justification", "project_narrative")),
]


@pytest.mark.parametrize(
    "rule, category, role",
    [(rule, category, role) for rule, category, roles in CONSISTENCY_RULES for role in roles],
)
def test_not_found_makes_consistency_rule_uncertain(facts, rule, category, role):
    # The remaining present values agree, so a naive comparison would "pass".
    change(facts, category, role, status="not_found")
    result = evaluate_rules(facts)[rule]
    assert result["outcome"] == "uncertain"
    assert result["evidence"][category][role] == {"value": None, "status": "not_found", "evidence_text": None}


@pytest.mark.parametrize("rule, category, roles", CONSISTENCY_RULES, ids=[r[0] for r in CONSISTENCY_RULES])
def test_all_inputs_not_found_is_uncertain(facts, rule, category, roles):
    for role in roles:
        change(facts, category, role, status="not_found")
    assert evaluate_rules(facts)[rule]["outcome"] == "uncertain"


def test_r03_not_found_with_disagreeing_values_is_a_finding(facts):
    # Increment 1B supersedes the 1A safeguard: a confident disagreement now outranks not_found.
    change(facts, "project_duration_months", "budget_justification", value=36)
    change(facts, "project_duration_months", "project_narrative", status="not_found")
    assert evaluate_rules(facts)["R03"]["outcome"] == "finding"


@pytest.mark.parametrize("blocking", ["missing", "unprocessable"])
@pytest.mark.parametrize("rule, category, roles", CONSISTENCY_RULES, ids=[r[0] for r in CONSISTENCY_RULES])
def test_missing_and_unprocessable_still_take_precedence_over_not_found(facts, rule, category, roles, blocking):
    change(facts, category, roles[0], status="not_found")
    change(facts, category, roles[1], status=blocking)
    assert evaluate_rules(facts)[rule]["outcome"] == "not_evaluated"


def test_not_found_does_not_change_other_rules(facts):
    change(facts, "personnel_effort_percent", "budget", status="not_found")
    results = evaluate_rules(facts)
    assert [results[rule]["outcome"] for rule in ("R01", "R02", "R03", "R04")] == ["pass", "uncertain", "pass", "pass"]


@pytest.mark.parametrize("cost_status,cost_value,just_status,expected", [
    # Required evidence not_found: never a pass or finding.
    ("not_found", None, "present", "uncertain"),
    ("not_found", None, "missing", "uncertain"),
    ("not_found", None, "not_found", "uncertain"),
    ("not_found", None, "unprocessable", "uncertain"),  # mirrors an uncertain cost
    ("present", 8000, "not_found", "uncertain"),
    # Existing missing/unprocessable precedence is preserved.
    ("missing", None, "not_found", "not_evaluated"),
    ("unprocessable", None, "not_found", "not_evaluated"),
    # A confirmed zero cost needs no justification, so its pass does not rely on not_found.
    ("present", 0, "not_found", "pass"),
])
def test_travel_outcomes_with_not_found(facts, cost_status, cost_value, just_status, expected):
    change(facts, "travel_cost_usd", "budget", status=cost_status, value=cost_value)
    change(facts, "travel_justification", "budget_justification", status=just_status,
           value="adequate" if just_status == "present" else None)
    assert evaluate_rules(facts)["R04"]["outcome"] == expected


def test_not_found_justification_is_not_treated_as_adequate(facts):
    change(facts, "travel_justification", "budget_justification", status="not_found")
    result = evaluate_rules(facts)["R04"]
    assert result["outcome"] not in ("pass", "finding")
    assert result["evidence"]["travel_justification"]["budget_justification"]["value"] is None


def test_not_found_keeps_result_contract(facts):
    change(facts, "project_duration_months", "project_narrative", status="not_found")
    results = evaluate_rules(facts)
    assert list(results) == ["R01", "R02", "R03", "R04"]
    assert all(set(result) == {"rule_name", "outcome", "message", "evidence"} for result in results.values())
    assert {result["outcome"] for result in results.values()} <= {"pass", "finding", "uncertain", "not_evaluated"}
    assert results["R03"]["outcome"] == "uncertain"
    assert results["R03"]["message"].startswith("Project duration consistency: ")


# --- R03 partial-evidence evaluation (Increment 1B) ------------------------------

DURATION = "project_duration_months"
DURATION_ROLES = ("budget", "budget_justification", "project_narrative")


def set_durations(facts, *inputs):
    """Set the three R03 inputs, each an int (present) or a status name."""
    for role, item in zip(DURATION_ROLES, inputs):
        if isinstance(item, int):
            change(facts, DURATION, role, value=item)
        else:
            change(facts, DURATION, role, status=item)


@pytest.mark.parametrize("inputs, expected", [
    ((24, 24, 24), "pass"),
    ((24, 36, 24), "finding"),
    ((24, 36, "not_found"), "finding"),
    ((24, 36, "uncertain"), "finding"),
    ((24, 36, "missing"), "finding"),
    ((24, 36, "unprocessable"), "finding"),
    ((24, 24, "not_found"), "uncertain"),
    ((24, 24, "uncertain"), "uncertain"),
    ((24, 24, "missing"), "not_evaluated"),
    ((24, 24, "unprocessable"), "not_evaluated"),
    (("not_found", "not_found", "not_found"), "uncertain"),
], ids=lambda value: "-".join(map(str, value)) if isinstance(value, tuple) else value)
def test_r03_precedence(facts, inputs, expected):
    set_durations(facts, *inputs)
    assert evaluate_rules(facts)["R03"]["outcome"] == expected


@pytest.mark.parametrize("inputs, expected", [
    ((24, "missing", "uncertain"), "not_evaluated"),        # missing outranks uncertain without a conflict
    (("unprocessable", "not_found", 24), "not_evaluated"),
    (("uncertain", "not_found", 24), "uncertain"),
    (("missing", "missing", "missing"), "not_evaluated"),
    ((36, "unprocessable", 24), "finding"),                 # conflict between budget and narrative
    ((1, 2, 3), "finding"),
])
def test_r03_additional_combinations(facts, inputs, expected):
    set_durations(facts, *inputs)
    assert evaluate_rules(facts)["R03"]["outcome"] == expected


def r03_message(facts, *inputs):
    set_durations(facts, *inputs)
    return evaluate_rules(facts)["R03"]["message"]


def test_r03_pass_message(facts):
    assert r03_message(facts, 24, 24, 24) == (
        "Project duration consistency: All three confident duration values agree (24 months)."
    )


def test_r03_finding_identifies_conflicting_roles_and_values(facts):
    assert r03_message(facts, 24, 36, 24) == (
        "Project duration consistency: Confident duration values disagree (Budget: 24 months; "
        "Budget Justification: 36 months; Project Narrative: 24 months); human review required."
    )


def test_r03_finding_mentions_unresolved_inputs(facts):
    assert r03_message(facts, 24, 36, "not_found") == (
        "Project duration consistency: Confident duration values disagree (Budget: 24 months; "
        "Budget Justification: 36 months); human review required. "
        "Also unresolved: Project Narrative (not_found)."
    )


def test_r03_uncertain_describes_partial_agreement(facts):
    assert r03_message(facts, 24, 24, "uncertain") == (
        "Project duration consistency: Budget and Budget Justification agree (24 months), but unresolved "
        "evidence prevents a complete comparison and no confident disagreement was established; "
        "human review required. Unresolved: Project Narrative (uncertain)."
    )


def test_r03_not_evaluated_describes_partial_agreement(facts):
    assert r03_message(facts, 24, "unprocessable", 24) == (
        "Project duration consistency: Budget and Project Narrative agree (24 months), but missing or "
        "unprocessable evidence prevents a complete comparison and no confident disagreement was "
        "established. Unresolved: Budget Justification (unprocessable)."
    )


def test_r03_single_and_no_confident_values(facts):
    assert "Only Budget has a confident value (24 months)" in r03_message(facts, 24, "not_found", "missing")
    message = r03_message(facts, "not_found", "not_found", "not_found")
    assert "No confident duration values are available" in message
    assert "Unresolved: Budget (not_found), Budget Justification (not_found), Project Narrative (not_found)." in message


def test_r03_singular_month(facts):
    assert "(1 month)" in r03_message(facts, 1, 1, 1)


def test_r03_never_treats_absent_values_as_zero(facts):
    # A naive comparison of 0 with None, or None with None, must not decide the outcome.
    set_durations(facts, "not_found", "missing", "uncertain")
    result = evaluate_rules(facts)["R03"]
    assert result["outcome"] == "not_evaluated"
    assert "0 months" not in result["message"]
    assert all(record["value"] is None for record in result["evidence"][DURATION].values())


def test_r03_message_contains_no_source_text(facts):
    set_durations(facts, 24, 36, "uncertain")  # change() gives uncertain records the excerpt "Test excerpt"
    message = evaluate_rules(facts)["R03"]["message"]
    assert "Test excerpt" not in message
    assert "Project Duration: 24 months" not in message


def test_r03_keeps_result_contract_and_evidence(facts):
    set_durations(facts, 24, 36, "not_found")
    result = evaluate_rules(facts)["R03"]
    assert set(result) == {"rule_name", "outcome", "message", "evidence"}
    assert result["rule_name"] == "Project duration consistency"
    assert list(result["evidence"]) == [DURATION]
    assert result["evidence"][DURATION] == facts[DURATION]


@pytest.mark.parametrize("inputs", [(24, 36, "not_found"), (24, 24, "missing"), ("not_found",) * 3])
def test_r03_changes_do_not_affect_other_rules(facts, inputs):
    baseline = {rule: result for rule, result in evaluate_rules(facts).items() if rule != "R03"}
    set_durations(facts, *inputs)
    assert {rule: result for rule, result in evaluate_rules(facts).items() if rule != "R03"} == baseline


def test_r01_and_r02_keep_previous_precedence(facts):
    # The shared R01/R02 function still lets unresolved evidence outrank a disagreement.
    change(facts, "personnel_cost_usd", "budget", value=24000)
    change(facts, "personnel_cost_usd", "budget_justification", status="not_found")
    change(facts, "personnel_effort_percent", "budget_justification", status="missing")
    results = evaluate_rules(facts)
    assert results["R01"]["outcome"] == "uncertain"
    assert results["R02"]["outcome"] == "not_evaluated"
    assert results["R01"]["message"] == "Personnel cost consistency: Evidence is uncertain; human review required."
