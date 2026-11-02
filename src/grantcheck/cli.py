"""Text-only command-line demonstration of GrantCheck on the sample scenarios.

Usage (from the repository root, with the src layout on the import path):
    python -m grantcheck.cli --scenario S02 [--details]

The CLI only maps a scenario ID to its sample_data folder and passes the file
paths to process_application(); intake, replay validation, and rule
evaluation all happen there. It makes no AI or network calls, writes nothing,
and never approves, rejects, or determines grant eligibility.

Exit codes:
    0  the review ran and a report was printed, whatever the outcome
       (findings, uncertain results, and processing exceptions included)
    1  execution error: a required scenario fixture is missing, or an
       unexpected error occurred (no report was produced)
    2  command-line usage error, such as an unknown scenario ID
"""

import argparse
import json
import sys
from pathlib import Path
from types import MappingProxyType

from grantcheck.orchestrator import process_application

EXIT_OK = 0
EXIT_UNEXPECTED_ERROR = 1
EXIT_USAGE_ERROR = 2

# src/grantcheck/cli.py -> repository root, independent of the working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"

# Explicit, case-sensitive scenario IDs and their folders under sample_data/.
SCENARIO_DIRECTORIES = MappingProxyType({
    "S01": "S01_consistent",
    "S02": "S02_personnel_cost_conflict",
    "S03": "S03_personnel_effort_conflict",
    "S04": "S04_duration_conflict",
    "S05": "S05_missing_travel_justification",
    "S06": "S06_ambiguous_travel_justification",
    "S07": "S07_unprocessable_document",
})

DOCUMENT_FILES = ("budget.txt", "budget_justification.txt", "project_narrative.txt")
REPLAY_FILE = "replay_facts.json"
REQUIRED_FIXTURE_FILES = DOCUMENT_FILES + (REPLAY_FILE,)

DISCLAIMER = (
    "GrantCheck provides candidate findings for human review. It does not approve, "
    "reject, or determine grant eligibility."
)
DETAILS_NOTICE = (
    "Details: potentially sensitive review information. "
    "Share only with authorized reviewers."
)
PROG = "python -m grantcheck.cli"
LABEL_WIDTH = 24


class _ArgumentParser(argparse.ArgumentParser):
    """argparse, but usage errors also end with the human-authority disclaimer."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE_ERROR, f"{self.prog}: error: {message}\n{DISCLAIMER}\n")


def build_parser():
    parser = _ArgumentParser(
        prog=PROG,
        description="Run GrantCheck on one sample scenario and print a text report. " + DISCLAIMER,
        epilog=(
            "Exit codes: 0 = review ran and a report was printed (including findings and "
            "processing exceptions); 1 = missing scenario fixture or unexpected execution "
            "error; 2 = usage error."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--scenario",
        required=True,
        choices=tuple(SCENARIO_DIRECTORIES),
        help="sample scenario ID, case-sensitive: S01 through S07",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="also print each rule's message and supporting evidence (potentially sensitive)",
    )
    return parser


# --- Report formatting ---------------------------------------------------------


def _field(label, value):
    return f"{label + ':':<{LABEL_WIDTH}}{value}"


def _format_error(error):
    """Show only the orchestrator's sanitized error fields."""
    text = f"[{error['stage']}] {error['reason']}"
    if "document" in error:
        text += f": {error['document']} document"
    if "message" in error:
        text += f": {error['message']}"
    if "rules" in error:
        text += f": {', '.join(error['rules'])}"
    return text


def _format_json(value):
    # ASCII-only JSON keeps strings quoted, line breaks escaped, and None as null.
    return json.dumps(value)


def _detail_lines(rule_results):
    lines = ["", DETAILS_NOTICE]
    if rule_results is None:
        lines.append("  not available (processing stopped before rule evaluation)")
        return lines
    for rule_id in sorted(rule_results):
        rule = rule_results[rule_id]
        lines.append(f"  {rule_id}  {rule['rule_name']}")
        lines.append(f"    Message: {rule['message']}")
        lines.append("    Evidence:")
        for category, sources in rule["evidence"].items():
            for role, record in sources.items():
                lines.append(
                    f"      {category} / {role}: status={record['status']}, "
                    f"value={_format_json(record['value'])}"
                )
                lines.append(f"        excerpt: {_format_json(record['evidence_text'])}")
    return lines


def format_report(scenario_id, result, details=False):
    """Return the text report for one process_application() result."""
    lines = [
        f"GrantCheck review: scenario {scenario_id}",
        "",
        _field("Application ID", result["application_id"]),
        _field("Processing status", result["processing_status"]),
        _field("Human review required", "yes" if result["review_required"] else "no"),
        _field("Finding count", result["finding_count"]),
        "",
        "Rule outcomes:",
    ]
    rule_results = result["rule_results"]
    if rule_results is None:
        lines.append("  not available (processing stopped before rule evaluation)")
    else:
        for rule_id in sorted(rule_results):
            rule = rule_results[rule_id]
            lines.append(f"  {rule_id}  {rule['outcome']:<14}{rule['rule_name']}")

    lines += ["", "Errors:"]
    if result["errors"]:
        lines += [f"  - {_format_error(error)}" for error in result["errors"]]
    else:
        lines.append("  none")

    if details:
        lines += _detail_lines(rule_results)

    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# --- Entry point ---------------------------------------------------------------


def _missing_fixtures(scenario_dir):
    """The scenario folder if it is missing, else any required file that is not a regular file."""
    if not scenario_dir.is_dir():
        return [scenario_dir]
    return [scenario_dir / name for name in REQUIRED_FIXTURE_FILES if not (scenario_dir / name).is_file()]


def _display_path(path):
    """Path relative to the repository (the folder containing sample_data/)."""
    try:
        return path.relative_to(SAMPLE_DATA_DIR.parent).as_posix()
    except ValueError:
        return path.name


def main(argv=None):
    args = build_parser().parse_args(argv)  # usage errors exit with EXIT_USAGE_ERROR
    scenario_dir = SAMPLE_DATA_DIR / SCENARIO_DIRECTORIES[args.scenario]

    missing = _missing_fixtures(scenario_dir)
    if missing:
        print(
            f"{PROG}: error: scenario {args.scenario} fixture not found: "
            f"{', '.join(_display_path(path) for path in missing)}. "
            f"No review report was produced.\n{DISCLAIMER}",
            file=sys.stderr,
        )
        return EXIT_UNEXPECTED_ERROR

    try:
        result = process_application(
            f"GC-{args.scenario}",
            *(scenario_dir / name for name in DOCUMENT_FILES),
            scenario_dir / REPLAY_FILE,
        )
    except Exception as error:  # noqa: BLE001 - report without a traceback or sensitive detail
        print(
            f"{PROG}: error: processing scenario {args.scenario} failed unexpectedly "
            f"({type(error).__name__}). No review report was produced.\n{DISCLAIMER}",
            file=sys.stderr,
        )
        return EXIT_UNEXPECTED_ERROR

    print(format_report(args.scenario, result, details=args.details))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
