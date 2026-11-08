# Final Solution Design Report — compliance addendum

This addendum is intended to be incorporated into the editable Stage 10 report before regenerating its PDF.

## Assignment alignment and implemented scope

The prototype implements a bounded Document Intelligence workflow for pre-award administrators: three synthetic UTF-8 application documents are registered; prerecorded candidate facts are validated; excerpts are resolved to exact source locations; and four deterministic consistency/completeness rules produce candidate findings for human review. This is an **offline replay-based AI-assisted design**, not a live model inference demonstration. The distinction is material to the course's AI-enabled prototype requirement.

## Requirements-to-implementation evidence

See `docs/REQUIREMENTS_TRACEABILITY.md` for a mapping of the instructor's deliverables to source modules, tests, demonstration paths, and remaining gaps. The original Assignment 1 and Assignment 2 documents remain the problem and architecture baseline; implementation differences and limitations should be treated as explicit scope refinements, not silently represented as complete.

## Demonstration and observed behavior

The documented local demonstration runs S01 (consistent), S02 (personnel-cost finding with source references), and S07 (processing exception). `scripts/demo_review.py` exercises S04's R03 correction lifecycle, authorization boundary, immutable snapshots, and SQLite recovery. The previous locally observed full regression run was **1,210 passed in 10.16 seconds**; replace this with the latest observed run before submission. These results demonstrate the synthetic fixtures and tested behaviors, not general AI extraction accuracy or reviewer usability.

## Repository and reproduction

GitHub repository: **[INSERT ACTUAL GITHUB URL]**. Reproduction path: README setup and `docs/DEMONSTRATION.md`; no deployment or recording link has been verified. Do not submit the placeholder as a working link.

## Human oversight and limitations

Candidate findings are not funding or eligibility decisions. Reviewer role checks are not user authentication. Human assertions are labeled separately from document-verified evidence. Stage 9 persists R03 as the correction snapshot baseline and recomputes all four rules for reports. Original documents are not persisted, and snapshot/event writes are separate transactions. Production use would require authenticated identity, stronger transaction guarantees, real-data security review, and validation of live extraction.

## AI assistance disclosure

Generative AI was used extensively in planning, code generation, tests, and drafting. The student is responsible for reviewing and validating outputs and for any final publication decisions. The exact extent of manual code review should be described truthfully rather than inferred from test results.
