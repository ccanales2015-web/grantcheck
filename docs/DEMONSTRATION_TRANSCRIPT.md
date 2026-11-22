# GrantCheck demonstration transcript

| | |
|---|---|
| Date captured | November 22, 2026 |
| Commit tested | `9c2e27066f15e7897f728ac93c6b32ed74398f68` |
| Source | A separate grading-test clone of https://github.com/ccanales2015-web/grantcheck, checked out at the tested commit with no local modifications |
| Environment | Windows 11; Python 3.13.0; pytest 9.2.0; Git 2.55.0.windows.5 |
| Setup | Clone from GitHub, created fresh on November 22, 2026 and subsequently fast-forwarded to commit `9c2e270`; new Python 3.13 virtual environment; `pip install -r requirements.txt`; commands run from the repository root with `PYTHONPATH=src` |
| Inputs | Synthetic scenarios only (`sample_data/`); no real grant applications and no live AI calls |

The commands are those listed in `docs/DEMONSTRATION.md`, run from Git Bash (hence `.venv/Scripts/python.exe` with forward slashes). Each block shows the command, its complete output, and its exit code. The output is reproduced verbatim; only the Markdown headings and code fences were added, and Windows line endings were normalized to LF.

**Note on line endings.** This Windows checkout used Git's CRLF line-ending conversion (`core.autocrlf=true`). The document SHA-256 prefixes and the character offsets shown in the S02 details therefore reflect CRLF bytes and will differ on an LF checkout (for example, on macOS or Linux). Line numbers, excerpts, extracted values, rule outcomes, findings, and exit codes are unaffected.

**AI assistance.** OpenAI ChatGPT (GPT-6) assisted with planning the demonstration and reviewing this transcript. Claude Opus 5.5 assisted with running the capture commands and formatting the captured output into this document. All test and demonstration results below are actual output produced by running GrantCheck at the tested commit; they were not generated or altered by an AI model.

## Summary

| # | Demonstration | Expected | Observed | Exit code |
|---|---|---|---|---|
| 1 | Automated test suite | All tests pass | 1210 passed in 8.85s | 0 |
| 2 | S01: consistent application | All four rules pass; no human review required | As expected | 0 |
| 3 | S02: conflicting personnel cost | R01 finding with source evidence; human review required | As expected | 0 |
| 4 | S07: unprocessable document | Processing exception; all four rules not evaluated; human review required | As expected | 0 |
| 5 | Human-review workflow (`scripts/demo_review.py`, scenario S04) | Unauthorized acceptance blocked; authorized reevaluation; SQLite recovery; no funding decision | As expected | 0 |

S07 demonstrates exception handling: GrantCheck stops evaluation and flags the application for human review. The human-review workflow itself (proposing, authorizing, and reevaluating a correction) is demonstrated separately in section 5 using scenario S04.

## 1. Automated test suite

```text
> .venv/Scripts/python.exe -m pytest -q
........................................................................ [  5%]
........................................................................ [ 11%]
........................................................................ [ 17%]
........................................................................ [ 23%]
........................................................................ [ 29%]
........................................................................ [ 35%]
........................................................................ [ 41%]
........................................................................ [ 47%]
........................................................................ [ 53%]
........................................................................ [ 59%]
........................................................................ [ 65%]
........................................................................ [ 71%]
........................................................................ [ 77%]
........................................................................ [ 83%]
........................................................................ [ 89%]
........................................................................ [ 95%]
..........................................................               [100%]
1210 passed in 8.85s
[exit code: 0]
```

## 2. Scenario S01: consistent application

```text
> .venv/Scripts/python.exe -m grantcheck.cli --scenario S01
GrantCheck review: scenario S01

Application ID:         GC-S01
Processing status:      completed
Human review required:  no
Finding count:          0

Rule outcomes:
  R01  pass          Personnel cost consistency
  R02  pass          Personnel effort consistency
  R03  pass          Project duration consistency
  R04  pass          Travel justification required for positive budget travel cost

Errors:
  none

GrantCheck provides candidate findings for human review. It does not approve, reject, or determine grant eligibility.
[exit code: 0]
```

## 3. Scenario S02: conflicting personnel cost (with details)

```text
> .venv/Scripts/python.exe -m grantcheck.cli --scenario S02 --details
GrantCheck review: scenario S02

Application ID:         GC-S02
Processing status:      completed
Human review required:  yes
Finding count:          1

Rule outcomes:
  R01  finding       Personnel cost consistency
  R02  pass          Personnel effort consistency
  R03  pass          Project duration consistency
  R04  pass          Travel justification required for positive budget travel cost

Errors:
  none

Details: potentially sensitive review information. Share only with authorized reviewers.
Source locations: lines are 1-based; offsets are 0-based character positions, end exclusive.
  R01  Personnel cost consistency
    Message: Personnel cost consistency: Potential inconsistency or missing justification; human review required.
    Evidence:
      personnel_cost_usd / budget: status=present, value=24000
        excerpt: "Personnel Cost: $24,000"
        location: budget.txt (sha256 9b4c08bd3704...), line 12, offsets 205-228
      personnel_cost_usd / budget_justification: status=present, value=20000
        excerpt: "Research Assistant is $20,000."
        location: budget_justification.txt (sha256 0ddb25e6528d...), line 16, offsets 421-451
  R02  Personnel effort consistency
    Message: Personnel effort consistency: No issue detected.
    Evidence:
      personnel_effort_percent / budget: status=present, value=50
        excerpt: "Personnel Effort: 50%"
        location: budget.txt (sha256 9b4c08bd3704...), line 13, offsets 230-251
      personnel_effort_percent / budget_justification: status=present, value=50
        excerpt: "at 50% effort throughout the project period."
        location: budget_justification.txt (sha256 0ddb25e6528d...), line 13, offsets 320-364
  R03  Project duration consistency
    Message: Project duration consistency: All three confident duration values agree (24 months).
    Evidence:
      project_duration_months / budget: status=present, value=24
        excerpt: "Project Duration: 24 months"
        location: budget.txt (sha256 9b4c08bd3704...), line 8, offsets 128-155
      project_duration_months / budget_justification: status=present, value=24
        excerpt: "The proposed project will be conducted over a 24-month period."
        location: budget_justification.txt (sha256 0ddb25e6528d...), line 9, offsets 172-234
      project_duration_months / project_narrative: status=present, value=24
        excerpt: "The research will be conducted over two years"
        location: project_narrative.txt (sha256 de193f115e48...), line 12, offsets 284-329
  R04  Travel justification required for positive budget travel cost
    Message: Travel justification required for positive budget travel cost: No issue detected.
    Evidence:
      travel_cost_usd / budget: status=present, value=8000
        excerpt: "Travel Cost: $8,000"
        location: budget.txt (sha256 9b4c08bd3704...), line 16, offsets 263-282
      travel_justification / budget_justification: status=present, value="adequate"
        excerpt: "These funds will support travel to coastal monitoring"
        location: budget_justification.txt (sha256 0ddb25e6528d...), line 21, offsets 539-592

GrantCheck provides candidate findings for human review. It does not approve, reject, or determine grant eligibility.
[exit code: 0]
```

## 4. Scenario S07: unprocessable document (exception handling)

```text
> .venv/Scripts/python.exe -m grantcheck.cli --scenario S07
GrantCheck review: scenario S07

Application ID:         GC-S07
Processing status:      exception
Human review required:  yes
Finding count:          0

Rule outcomes:
  R01  not_evaluated Personnel cost consistency
  R02  not_evaluated Personnel effort consistency
  R03  not_evaluated Project duration consistency
  R04  not_evaluated Travel justification required for positive budget travel cost

Errors:
  - [rule_evaluation] rules_not_evaluated: R01, R02, R03, R04

GrantCheck provides candidate findings for human review. It does not approve, reject, or determine grant eligibility.
[exit code: 0]
```

## 5. Human-review workflow (scenario S04)

```text
> .venv/Scripts/python.exe scripts/demo_review.py
1. Initial R03 outcome: finding
2. Unauthorized staff acceptance: blocked
3. Authorized reevaluation: snapshot 1
4. Revised budget duration: 36
5. Assertion document-verified: False
6. SQLite recovery: two immutable snapshots found
7. Grant funding decision: none (human authority retained)
Synthetic review demonstration completed successfully.
[exit code: 0]
```
