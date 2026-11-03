
"""Command-line interface for GrantCheck's deterministic extraction workflow.

This module is separate from the existing replay CLI.
It never makes grant approval or eligibility decisions.
"""

import argparse
import sys

from grantcheck.document_intake import intake_application
from grantcheck.extraction_orchestrator import process_extracted_application

DISCLAIMER = (
    "GrantCheck provides candidate findings for human review. It does not approve, "
    "reject, or determine grant eligibility."
)


class _ArgumentParser(argparse.ArgumentParser):
    """Include GrantCheck's human-authority disclaimer in usage errors."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: error: {message}\n{DISCLAIMER}\n",
        )


def main(argv=None):
    """Entry point for the extraction CLI."""
    parser = _ArgumentParser(
        description="Run GrantCheck's deterministic extraction workflow."
    )
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--budget", required=True)
    parser.add_argument("--justification", required=True)
    parser.add_argument("--narrative", required=True)

    args = parser.parse_args(argv)

    application = intake_application(
        args.application_id,
        args.budget,
        args.justification,
        args.narrative,
    )
    run = process_extracted_application(application)


    response = run.response

    print("GrantCheck extraction review")
    print(f"Processing run ID: {response['processing_run_id']}")

    if response["failure_code"] is not None:
        print("Processing status: exception")
        print(f"Failure code: {response['failure_code']}")
        print("Rule outcomes: not available")
        print("Human review required: processing could not be completed")
    else:
        print(f"Application ID: {response['application_id']}")
        print(f"Processing status: {response['processing_status']}")
        print(
            "Human review required: "
            + ("yes" if response["review_required"] else "no")
        )

        for rule_id, rule in sorted(response["rule_results"].items()):
            print(f"{rule_id}: {rule['outcome']}")

    print(DISCLAIMER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
