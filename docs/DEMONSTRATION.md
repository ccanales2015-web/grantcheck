# Reproducible GrantCheck demonstration

All inputs are synthetic. Run from the repository root in PowerShell after installing the dependencies and setting `$env:PYTHONPATH = "src"`.

```powershell
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S01
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S02 --details
.\.venv\Scripts\python.exe -m grantcheck.cli --scenario S07
.\.venv\Scripts\python.exe scripts/demo_review.py
.\.venv\Scripts\python.exe -m pytest -q
```

Expected observations:

- **S01:** all four rules pass; no review needed.
- **S02:** R01 flags inconsistent personnel costs; `--details` shows source excerpts and locations.
- **S07:** processing exception; rules cannot be evaluated reliably.
- **Review script:** S04 initially flags R03, staff cannot accept their own proposal without the authorized role, authorized reviewer accepts and reevaluates a clearly labeled human assertion, immutable snapshot sequence advances from 0 to 1, and reopening the temporary SQLite file restores both snapshots. Funding decision remains `None`.

The review demo is a *Python API* demonstration, not a standalone interface or authenticated production workflow. It uses a temporary SQLite database that is removed on completion. It does not make live AI calls or use real grant applications.

Capture a screen recording or terminal transcript if required by the instructor. Do not claim live extraction, production security, or a real stakeholder usability study.
