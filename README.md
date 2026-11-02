# GrantCheck

**AI-Assisted Cross-Document Grant Application Review**

GrantCheck is a prototype that helps pre-award research administrators find potential inconsistencies and missing information across the documents of a grant application. It compares facts stated in different documents and reports candidate findings for a human reviewer to examine.

The current prototype runs fully offline. Instead of calling a live AI model, it loads pre-recorded candidate facts ("replay" files) for seven synthetic sample applications, then validates and evaluates them deterministically.

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
document intake -> replay loading and validation -> rule evaluation -> orchestration -> CLI report
```

| Stage | Module (`src/grantcheck/`) | Responsibility |
|---|---|---|
| Document intake | `document_intake.py` | Reads the three documents as strict UTF-8 text (at most 1,048,576 bytes each), records each document's exact text and SHA-256 fingerprint, and reports per-document errors (`missing`, `unreadable`, `file_too_large`, `empty`) without stopping at the first one. |
| Fact schema | `fact_schema.py` | Defines the six candidate-fact categories, which documents each is read from (12 source records in total), the allowed statuses (`present`, `missing`, `uncertain`, `unprocessable`), and value types. |
| Replay loading and validation | `replay_loader.py`, `fact_validation.py` | Loads a `replay_facts.json` file of pre-recorded candidate facts. Enforces a 1 MiB size limit, strict UTF-8 and JSON (no byte order mark, duplicate keys, or `NaN`/`Infinity`), the exact schema, the application ID match, and value rules. Every evidence excerpt must occur verbatim in the text captured at intake. |
| Rule evaluation | `rule_engine.py` | Applies rules R01–R04 deterministically to validated candidate facts and returns each rule's outcome, message, and supporting evidence. |
| Orchestration | `orchestrator.py` | `process_application()` runs the stages in order and returns one summary: processing status, whether human review is required, rule results, finding count, and structured errors. Validation details are replaced by fixed public messages so results do not expose replay content. |
| CLI reporting | `cli.py` | Text-only command-line demonstration that runs one sample scenario and prints a report. |

Processing status and review rules:

- **completed, no review required**: all four rules `pass`.
- **completed, review required**: at least one `finding` or `uncertain` outcome, and no `not_evaluated` outcome.
- **exception, review required**: any rule is `not_evaluated`, or a document failed intake, or the replay data could not be validated. Results that were produced are kept for the reviewer.

## Human Authority and Safeguards

GrantCheck produces **candidate findings for human review only**. It does not approve or reject applications, determine grant eligibility, or resolve discrepancies. Every report ends with this disclaimer:

> GrantCheck provides candidate findings for human review. It does not approve, reject, or determine grant eligibility.

Additional safeguards in the implementation:

- Candidate facts must quote verbatim evidence from the submitted documents; values without traceable evidence are rejected.
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
- **`--details`** also shows each rule's message and supporting evidence: for each source document, the fact's status, normalized value, and verbatim evidence excerpt. This section is labelled as **potentially sensitive review information** because the excerpts quote application documents. Full source-document text is never printed.

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

## Running the Tests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite runs offline and includes unit tests for each module, schema checks for the sample fixtures, integration tests across all seven scenarios, and CLI tests that run the real command-line entry point. Set `$env:PYTHONPATH = "src"` first if you run an individual test file on its own.

## Known Limitations

- **Offline replay, not live extraction.** Candidate facts are pre-recorded in `replay_facts.json` files. No AI model is called, so passing tests demonstrates validation and rule logic, not the accuracy of live AI extraction.
- **Plain-text input only.** Documents must be UTF-8 text files of at most 1,048,576 bytes. PDF, Word, and other formats are not parsed.
- **No persistence, deployment, or graphical interface.** Results are printed to the terminal and not stored. There is no web or desktop interface, and the package is not installed or deployed.
- **Limited demonstration scope.** The CLI runs only the seven bundled synthetic scenarios, and only the four checks above are implemented.
- **No grant decisions.** GrantCheck never approves, rejects, or determines eligibility, and it does not resolve the discrepancies it reports.

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

Expected: the tests pass; S01 reports all rules passing with no review required; S02 reports an R01 personnel cost finding and shows its evidence; S07 reports a processing exception with all four rules not evaluated. All three CLI runs exit with code 0.

## Academic Context

CEN 5035 / CEN 4010 — Principles of Software Engineering

Final Project, Fall 2026
