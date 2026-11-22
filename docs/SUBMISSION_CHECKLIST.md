# Final submission checklist (owner verification required)

The professor's Final Project instructions require a GitHub repository, a report linking to the repository and demonstration, a runnable prototype, and presentation/portfolio evidence.

- [x] Repository published to GitHub at https://github.com/ccanales2015-web/grantcheck (currently **private**).
- [ ] Before submission, decide on visibility: make the repository public after a final review for credentials, private data, and generated files, or otherwise confirm graders can access it.
- [x] Root-level MIT `LICENSE` added (copyright holder: `ccanales2015-web`).
- [ ] Review `CONTRIBUTING.md`, `README.md`, and `.gitignore`; confirm secrets and real data are absent.
- [x] Verify a clean setup on a fresh machine or virtual environment using the README. Verified Nov 22, 2026: cloned from GitHub into a separate directory, created a fresh Python 3.13 virtual environment, and installed `requirements.txt` successfully; the S01, S02, and S07 CLI demonstrations and `scripts/demo_review.py` completed successfully.
- [x] Run the full pytest suite locally and record the **new** test count/date. Verified Nov 22, 2026 in the fresh clone: 1,210 passed in 10.22s. Previous user-observed result: 1,210 passed in 10.16s (Nov 7, 2026).
- [x] Run the five commands in `docs/DEMONSTRATION.md`; save a transcript or short recording. Saved Nov 22, 2026 as [`DEMONSTRATION_TRANSCRIPT.md`](DEMONSTRATION_TRANSCRIPT.md): all 1,210 tests passed, and the S01, S02, and S07 scenario demonstrations and the S04 human-review workflow (`scripts/demo_review.py`) completed successfully.
- [ ] Update the PDF Solution Design Report with the actual repository URL, demonstration path, requirements traceability, and observed results.
- [ ] Verify slide claims match the implemented offline, R03-baseline, API-only review workflow.
- [ ] Describe material AI assistance and the student's own review and validation accurately.
- [ ] Verify final deliverable format and submission portal requirements.

**Known limitations to retain:** replay rather than live extraction; UTF-8 text only; synthetic scenarios; no authenticated reviewer identities; R03 persisted snapshot baseline; separate snapshot/ledger transactions; no deployed UI.
