"""Reproducible synthetic review workflow demonstration (no real data)."""
from pathlib import Path
from tempfile import TemporaryDirectory

from grantcheck.document_intake import intake_application
from grantcheck.review_authority import ReviewerAuthorizationError
from grantcheck.review_workflow import resume_review, start_review
from grantcheck.sqlite_persistence import SQLiteReviewStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data" / "S04_duration_conflict"
STAFF = {"reviewer_id": "demo-staff", "roles": ["staff_reviewer"]}
AUTHORIZED = {"reviewer_id": "demo-authorized", "roles": ["authorized_reviewer"]}

def main():
    with TemporaryDirectory(prefix="grantcheck_demo_") as directory:
        db_path = Path(directory) / "review.sqlite3"
        with SQLiteReviewStore(db_path) as store:
            session = start_review(
                store, "GC-S04", SAMPLE / "budget.txt",
                SAMPLE / "budget_justification.txt",
                SAMPLE / "project_narrative.txt", SAMPLE / "replay_facts.json",
            )
            run_id = session.processing_run_id
            initial = session.report()
            assert initial["rule_results"]["R03"]["outcome"] == "finding"
            assert initial["snapshot_sequence"] == 0
            print("1. Initial R03 outcome:", initial["rule_results"]["R03"]["outcome"])
            proposal = session.submit(
                STAFF, fact_category="project_duration_months",
                document_role="budget", operation="replace", proposed_value=36,
                reason="Synthetic demonstration of revised project duration.",
                source_type="human_assertion",
                source_details={"assertion_basis": "Synthetic reviewer statement; not document-verified."},
            )
            try:
                session.decide(proposal.proposal_id, STAFF, "accepted")
            except ReviewerAuthorizationError:
                print("2. Unauthorized staff acceptance: blocked")
            else:
                raise AssertionError("staff acceptance was unexpectedly permitted")
            session.decide(proposal.proposal_id, AUTHORIZED, "accepted")
            session.reevaluate(proposal.proposal_id, AUTHORIZED)
            revised = session.report()
            assert revised["snapshot_sequence"] == 1
            assert revised["candidate_facts"]["project_duration_months"]["budget"]["value"] == 36
            assert revised["correction_history"][0]["document_verified"] is False
            assert revised["funding_decision"] is None
            print("3. Authorized reevaluation: snapshot", revised["snapshot_sequence"])
            print("4. Revised budget duration:", revised["candidate_facts"]["project_duration_months"]["budget"]["value"])
            print("5. Assertion document-verified:", revised["correction_history"][0]["document_verified"])
        original = intake_application(
            "GC-S04", SAMPLE / "budget.txt",
            SAMPLE / "budget_justification.txt", SAMPLE / "project_narrative.txt",
        )
        with SQLiteReviewStore(db_path) as store:
            resumed = resume_review(store, run_id, original)
            assert resumed.report()["snapshot_sequence"] == 1
            assert len(store.load_snapshots(run_id)) == 2
            print("6. SQLite recovery: two immutable snapshots found")
        print("7. Grant funding decision: none (human authority retained)")
    print("Synthetic review demonstration completed successfully.")

if __name__ == "__main__":
    main()
