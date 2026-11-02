# GrantCheck

**AI-Assisted Cross-Document Grant Application Review**

GrantCheck is a prototype that helps pre-award research administrators find potential inconsistencies and missing information across the documents of a grant application. It compares facts stated in different documents and reports candidate findings for a human reviewer to examine.

The current prototype runs fully offline. Instead of calling a live AI model, it loads pre-recorded candidate facts ("replay" files) for seven synthetic sample applications, then validates and evaluates them deterministically. Each candidate fact's evidence is traced to an exact location in a fingerprinted source document so a reviewer can inspect it.

## Intended Users

Pre-award research administrators who review grant application documents before submission and need to spot cross-document discrepancies quickly. GrantCheck supports their review; it does not replace it.

## Scope

The prototype reviews three document types:

- Budget
- Budget Justification
- Project Narrative

It applies four governed cross-document checks:

| Rule | Check | Compares |
|---|---|---|
| R01 | Personnel cost consistency | Personnel cost in the Budget and Budget Justification |
| R02 | Personnel effort consistency | Personnel effort percentage in the Budget and Budget Justification |
| R03 | Project duration consistency | Project duration in months across all three documents |
| R04 | Travel justification required for positive budget travel cost (travel justification completeness) | Whether a positive Budget travel cost has an adequate travel justification in the Budget Justification |

Each rule produces one of four outcomes: `pass`, `finding` (a potential inconsistency or missing justification), `uncertain` (the evidence is ambiguous), or `not_evaluated` (there is not enough usable information to evaluate the rule).

## Architecture and Processing Workflow

```
document intake -> replay loading and validation -> provenance resolution -> rule evaluation -> CLI report
```

`orchestrator.process_application()` runs the first four stages in that order and returns one summary, which the CLI presents.

| Stage | Module (`src/grantcheck/`) | Responsibility |
|---|---|---|
| Document intake | `document_intake.py` | Reads the three documents as strict UTF-8 text (at most 1,048,576 bytes each), records each document's exact text and SHA-256 fingerprint, and reports per-document errors (`missing`, `unreadable`, `file_too_large`, `empty`) without stopping at the first one. |
| Fact schema | `fact_schema.py` | Defines the six candidate-fact categories, which documents each is read from (12 source records in total), the allowed statuses (`present`, `missing`, `uncertain`, `unprocessable`), and value types. |
| Replay loading and validation | `replay_loader.py`, `fact_validation.py` | Loads a `replay_facts.json` file of pre-recorded candidate facts. Enforces a 1 MiB size limit, strict UTF-8 and JSON (no byte order mark, duplicate keys, or `NaN`/`Infinity`), the exact schema, the application ID match, and value rules. Every evidence excerpt must occur verbatim in the text captured at intake. |
| Provenance resolution | `provenance.py` | After replay loading and before rule evaluation, locates every evidence excerpt in the text stored at intake and records the source file name, document SHA-256, occurrence count, and exact offsets and line numbers. See [Source Provenance](#source-provenance). |
| Rule evaluation | `rule_engine.py` | Applies rules R01–R04 deterministically to validated candidate facts and returns each rule's outcome, message, and supporting evidence. |
| Orchestration | `orchestrator.py` | `process_application()` runs the stages in order and returns one summary with eight top-level fields: `application_id`, `processing_status`, `review_required`, `candidate_facts`, `rule_results`, `finding_count`, `errors`, and `provenance`. Validation details are replaced by fixed public messages so results do not expose replay content. |
| CLI reporting | `cli.py` | Text-only command-line demonstration that runs one sample scenario and prints a report. |

Processing status and review rules:

- **completed, no review required**: all four rules `pass`.
- **completed, review required**: at least one `finding` or `uncertain` outcome, and no `not_evaluated` outcome.
- **exception, review required**: any rule is `not_evaluated`, or a document failed intake, or the replay data could not be validated, or source provenance could not be resolved (rules are then not run). Results that were produced are kept for the reviewer.

## Source Provenance

Provenance is returned as a separate map in `result["provenance"]`, keyed by the same fact categories and document roles as the candidate facts. The existing candidate-fact records (`value`, `status`, `evidence_text`) and rule-result records (`rule_name`, `outcome`, `message`, `evidence`) are unchanged.

```python
result["provenance"][fact_category][document_role] = {
    "filename": str,             # base name only, e.g. "budget_justification.txt"
    "document_sha256": str,      # full SHA-256 of the original document bytes
    "occurrence_count": int,     # exact number of times the evidence excerpt occurs
    "locations": [               # at most 100, in ascending offset order
        {
            "start_offset": int,
            "end_offset": int,
            "start_line": int,
            "end_line": int,
        }
    ],
}
```

- **Filenames** are base names without directory components.
- **SHA-256 fingerprints** identify the exact original bytes of each ingested document.
- **Offsets** are zero-based Unicode code-point indices into the document text; `end_offset` is exclusive, so `text[start_offset:end_offset]` equals the evidence excerpt.
- **Lines** are one-based and counted by `\n`. `end_line` is the line holding the excerpt's final character, so a multiline excerpt has `end_line > start_line`.
- **Matching** is exact: case-, whitespace-, and line-ending-sensitive. Overlapping occurrences are counted.
- **Repeated excerpts:** at most 100 locations are stored per excerpt, while `occurrence_count` stays exact.
- **Null evidence** (`missing` and `unprocessable` records) produces `occurrence_count: 0` and no locations; the source filename and SHA-256 are still recorded.
- **No rereading:** the resolver uses the text stored at intake and never rereads source files. It fails explicitly if an excerpt does not occur in its document or a document's hash does not match its stored text.
- `provenance` is `None` when intake fails, when replay data cannot be validated, or when provenance cannot be resolved.

The evidence excerpt itself is kept only in `candidate_facts`; it is not duplicated in the provenance map.

## Human Authority and Safeguards

GrantCheck produces **candidate findings for human review only**. It does not approve or reject applications, determine grant eligibility, or resolve discrepancies. Every report ends with this disclaimer:

> GrantCheck provides candidate findings for human review. It does not approve, reject, or determine grant eligibility.

Additional safeguards in the implementation:

- Candidate facts must quote verbatim evidence from the submitted documents; values without traceable evidence are rejected. Each excerpt's location and document fingerprint are reported so a reviewer can check it.
- Missing or unprocessable evidence is never given an invented source location.
- Ambiguous or unusable information is reported as `uncertain` or `not_evaluated`, not guessed.
- Error output uses structured, sanitized fields. Full document text, raw validation messages, and tracebacks are not printed during ordinary processing failures.
- No network connection, external AI service, or data persistence is used.

## Technology and Prerequisites

- **Python 3.13.0** was used for development and testing.
- **pytest 9.2.0** (the only entry in `requirements.txt`) is needed to run the tests.
- The application itself uses only the Python standard library.
- No external AI API, API key, or network connection is required.

## Setup (Windows PowerShell)

Run these commands from the repository root:

```powershell
# Create a virtual environment (requires Python 3.13 on PATH; "py -3.13 -m venv .venv" also works)
python -m venv .venv

# Install the test dependency
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Make the src/ package importable
$env:PYTHONPATH = "src"
```

The `grantcheck` package lives in `src/` and is **not installed** into the virtual environment. Setting `$env:PYTHONPATH = "src"` lets Python find it, but only for the **current PowerShell session**. Run that command again in each new session before using the CLI. The commands in this README call `.\.venv\Scripts\python.exe` directly, so the virtual environment does not need to be activated.

## Running the CLI Demonstration

With `$env:PYTHONPATH = "src"` set in the current session:

```powershell
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S01
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S02 --details
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S07
```

`--scenario` accepts `S01` through `S07` (case-sensitive). Run with `--help` for usage information.

- **Summary mode** (default) shows the application ID, processing status, whether human review is required, the finding count, the outcome of each of the four rules, any structured errors, and the disclaimer.
- **`--details`** also shows each rule's message and supporting evidence: for each source document, the fact's status, normalized value, verbatim evidence excerpt, and its source location. This section is labelled as **potentially sensitive review information** because the excerpts quote application documents. Full source-document text is never printed.

The summary output is unchanged by provenance; source locations appear only with `--details`. For example, part of the S02 detailed report:

```
      personnel_cost_usd / budget: status=present, value=24000
        excerpt: "Personnel Cost: $24,000"
        location: budget.txt (sha256 9b4c08bd3704...), line 12, offsets 205-228
      personnel_cost_usd / budget_justification: status=present, value=20000
        excerpt: "Research Assistant is $20,000."
        location: budget_justification.txt (sha256 0ddb25e6528d...), line 16, offsets 421-451
```

Offsets and fingerprints depend on the exact file bytes, so they differ between checkouts with CRLF line endings (shown above, from Windows) and LF line endings; line numbers do not.

How `--details` presents each kind of evidence:

| Evidence | Displayed as |
|---|---|
| Located once | `location: <file> (sha256 <first 12 hex>...), line N, offsets a-b` (`lines N-M` for a multiline excerpt) |
| Uncertain | `location (uncertain evidence): ...`, so a verified location is not presented as confirmed information |
| Repeated | `possible locations: <file> ...; excerpt occurs K times, not uniquely identified`, followed by one line per location |
| More than 100 occurrences | the same, ending `; showing first 100`, with only the 100 stored locations listed |
| Missing or unprocessable | `source document: <file> (sha256 <first 12 hex>...); no evidence location` |
| Provenance unavailable | `source: not available (provenance not resolved)` |

A line above the evidence explains the units: lines are 1-based; offsets are 0-based character positions, end exclusive. The CLI shows only the first 12 hexadecimal characters of each SHA-256; the full fingerprint is in `result["provenance"]` and is what should be used to verify a document.

### CLI exit codes

| Code | Meaning |
|---|---|
| `0` | A review report was generated, including reports with findings, uncertain results, or expected processing exceptions such as S07. Findings are not treated as program failures. |
| `1` | Execution error: a required scenario fixture file or folder is missing, or an unexpected error occurred. No report is produced. |
| `2` | Command-line usage error, such as an unknown or lowercase scenario ID. |

## Sample Scenarios

Each folder in `sample_data/` holds a synthetic application: the three documents (`budget.txt`, `budget_justification.txt`, `project_narrative.txt`), the pre-recorded candidate facts (`replay_facts.json`), and the expected results (`expected_results.json`) used by the tests.

| ID | Folder | Purpose | Expected outcome |
|---|---|---|---|
| S01 | `S01_consistent` | Fully consistent baseline | completed; all rules pass; no review required |
| S02 | `S02_personnel_cost_conflict` | Budget personnel cost $24,000 vs. $20,000 in the justification | completed; R01 finding; review required |
| S03 | `S03_personnel_effort_conflict` | Budget effort 50% vs. 25% in the justification | completed; R02 finding; review required |
| S04 | `S04_duration_conflict` | Justification states a three-year period; Budget and Narrative state 24 months / two years | completed; R03 finding; review required |
| S05 | `S05_missing_travel_justification` | Travel cost budgeted, but the justification has no travel section | completed; R04 finding; review required |
| S06 | `S06_ambiguous_travel_justification` | Travel justification present but vague | completed; R04 uncertain; 0 findings; review required |
| S07 | `S07_unprocessable_document` | Budget Justification is readable text that contains only an unreadable-content placeholder | exception; all rules not evaluated; review required |

## Provenance Evaluation

A read-only evaluation ran all seven scenarios through `process_application()` and checked every provenance record independently against the files' bytes: the filename, the full SHA-256, the exact text of each stored span, and each line number. Finding-level traceability was also checked against the actual `--details` output.

| Metric | Result |
|---|---|
| Candidate-fact source records | 84 |
| Evidence-bearing records | 76 |
| Evidence location coverage | 76/76 (100%) |
| Unique location coverage | 76/76 (100%) |
| Records without evidence | 8 |
| Actual candidate findings | 4 |
| Finding-level traceability | 4/4 (100%) |
| Regression tests | 574 passed |

How the figures were counted:

- **Source records:** 7 scenarios × 12 records = 84. Records are counted separately for each scenario, because each scenario's documents are separate files with their own fingerprints. Although there are 76 evidence-bearing records, there are only 16 distinct excerpt strings across the dataset.
- **Evidence-bearing records** have a non-null `evidence_text` (75 `present` and 1 `uncertain`). The 8 records without evidence are 2 `missing` (S05) and 6 `unprocessable` (S07); each has no locations but keeps its document's filename and SHA-256.
- **Evidence location coverage** counts evidence-bearing records with at least one location whose span matches the excerpt exactly. **Unique location coverage** counts those with exactly one occurrence; no record had multiple or zero occurrences.
- **Findings** are rule outcomes equal to `finding`; `pass`, `uncertain`, and `not_evaluated` outcomes are not counted. The four findings are S02 R01, S03 R02, S04 R03, and S05 R04. A finding counts as traceable when every evidence-bearing source it cites has a verified location and every source without evidence is identified by its document with no invented location, both in the provenance map and in the `--details` report. Together the four findings cite 9 source records: 8 with verified locations and 1 identified by document only.
- **S05 (missing information):** the budgeted travel cost has a verified location, while the missing justification is associated with `budget_justification.txt` and its fingerprint without inventing an evidence location.
- **S06** keeps its evidence labelled uncertain: R04 is `uncertain`, not a finding, and the CLI shows `location (uncertain evidence)`.
- **S07** keeps its processing-exception behavior: all four rules are `not_evaluated`, and its unprocessable Budget Justification records have no locations, only the document's identity.

### Requirements interpretation

- **FR-02 — Candidate Fact Extraction.** The prototype now retains verifiable source locations for replay-loaded candidate facts, and uncertain or unprocessable evidence is not presented as confirmed information (S06, S07). It does not perform live AI extraction: the candidate facts are pre-recorded replay inputs.
- **NFR-01 — Source Traceability.** In the seven synthetic replay scenarios, all four candidate findings were traceable to verified source evidence or to an identified document where required evidence was absent. This demonstrates the prototype's provenance mechanism on the evaluated dataset, not general NFR-01 compliance.

### Exploratory timing

On one development machine, `process_application()` (intake, replay loading, provenance resolution, and rule evaluation) took a median of approximately 1.31–1.34 ms per scenario over 50 in-process runs, and one sequential pass over all seven scenarios took approximately 9.33 ms. These are exploratory measurements with small synthetic files that exclude interpreter startup and CLI process launch. They are not a formal NFR-07 performance validation.

## Running the Tests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite runs offline and includes unit tests for each module (including repeated, overlapping, multiline, LF/CRLF, and more-than-100-occurrence provenance cases), schema checks for the sample fixtures, integration tests across all seven scenarios, and CLI tests that run the real command-line entry point. A passing suite shows the implemented behavior is consistent with its tests; it does not by itself establish compliance with the requirements above. Set `$env:PYTHONPATH = "src"` first if you run an individual test file on its own.

## Known Limitations

- **Offline replay, not live extraction.** Candidate facts are pre-recorded in `replay_facts.json` files. No AI model is called, so passing tests demonstrates validation and rule logic, not the accuracy of live AI extraction.
- **Plain-text input only.** Documents must be UTF-8 text files of at most 1,048,576 bytes. PDF, Word, and other formats are not parsed.
- **No persistence, deployment, or graphical interface.** Results are printed to the terminal and not stored. There is no web or desktop interface, and the package is not installed or deployed.
- **Limited demonstration scope.** The CLI runs only the seven bundled synthetic scenarios, and only the four checks above are implemented.
- **No grant decisions.** GrantCheck never approves, rejects, or determines eligibility, and it does not resolve the discrepancies it reports.
- **Small, synthetic evaluation dataset.** The provenance results above come from seven synthetic scenarios with 16 distinct excerpts. All evaluated excerpts are unique and single-line; repeated and multiline excerpts are covered by unit tests rather than by the seven scenarios.
- **Location, not correctness.** Provenance establishes where an excerpt occurs, not whether the extracted or normalized value is correct (for example, that "two years" means 24 months).
- **No persistent identifiers or review history.** There are no fact IDs, finding IDs, or records of human review decisions.
- **Offsets depend on the exact text.** Character offsets depend on the document's exact text and line endings; line numbers are more portable.
- **Abbreviated CLI fingerprints.** The 12-character hash shown by the CLI is not a substitute for checking the full SHA-256 in the provenance map.
- **No location for missing information.** Missing information cannot have a genuine source-text location; GrantCheck identifies the document where it was expected instead.
- **Reviewer usability not independently evaluated.** End-to-end usability for reviewers has not been tested with users or reviewed externally.

## Repository Structure

```
grantcheck/
├── README.md
├── requirements.txt          # pytest==9.2.0
├── sample_data/              # seven synthetic scenarios (S01–S07)
│   └── S0x_<name>/
│       ├── budget.txt
│       ├── budget_justification.txt
│       ├── project_narrative.txt
│       ├── replay_facts.json
│       └── expected_results.json
├── src/
│   └── grantcheck/           # application package (standard library only)
│       ├── document_intake.py
│       ├── fact_schema.py
│       ├── fact_validation.py
│       ├── replay_loader.py
│       ├── provenance.py
│       ├── rule_engine.py
│       ├── orchestrator.py
│       └── cli.py
└── tests/                    # pytest suite
```

## Reproducible Demonstration Path

In a new PowerShell window at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S01
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S02 --details
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S07
```

Expected: the tests pass; S01 reports all rules passing with no review required; S02 reports an R01 personnel cost finding and shows its evidence with source locations; S07 reports a processing exception with all four rules not evaluated. All three CLI runs exit with code 0.

## Academic Context

CEN 5035 / CEN 4010 — Principles of Software Engineering

Final Project, Fall 2026
