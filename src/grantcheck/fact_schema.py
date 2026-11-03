"""Authoritative candidate-fact schema definitions for GrantCheck.

These constants define which fact categories exist, which source documents
each one is read from, what type a present value must have, and the fields
and statuses of a source record. They are definitions only: validation,
extraction, and file reading live in other modules that import from here.

Note for validators: bool is a subclass of int in Python, so an isinstance
check against these type specifications must reject True/False separately.
"""

from types import MappingProxyType

from grantcheck.document_intake import DOCUMENT_ROLES

# Document roles, named for readability; DOCUMENT_ROLES remains the single definition.
BUDGET, BUDGET_JUSTIFICATION, PROJECT_NARRATIVE = DOCUMENT_ROLES

# Each fact category and the document roles it is read from, in role order.
FACT_SOURCES = MappingProxyType({
    "project_duration_months": (BUDGET, BUDGET_JUSTIFICATION, PROJECT_NARRATIVE),
    "personnel_role": (BUDGET, BUDGET_JUSTIFICATION),
    "personnel_cost_usd": (BUDGET, BUDGET_JUSTIFICATION),
    "personnel_effort_percent": (BUDGET, BUDGET_JUSTIFICATION),
    "travel_cost_usd": (BUDGET, BUDGET_JUSTIFICATION),
    "travel_justification": (BUDGET_JUSTIFICATION,),
})

# Required Python type of a value whose status is "present".
FACT_VALUE_TYPES = MappingProxyType({
    "project_duration_months": int,
    "personnel_role": str,
    "personnel_cost_usd": (int, float),
    "personnel_effort_percent": (int, float),
    "travel_cost_usd": (int, float),
    "travel_justification": str,
})

# Categories whose present value is restricted to a fixed vocabulary.
FACT_ALLOWED_VALUES = MappingProxyType({
    "travel_justification": ("adequate",),
})

# "not_found": no supported statement was recognized. Unlike "missing", it does
# not confirm the information is absent. Like "missing", its value and
# evidence_text are null.
FACT_STATUSES = ("present", "missing", "uncertain", "unprocessable", "not_found")

SOURCE_RECORD_FIELDS = ("value", "status", "evidence_text")

# Reserved for future extraction metadata kept separately from candidate-fact
# records; source records never carry a reason code.
NOT_FOUND_REASON_CODES = ("unsupported_fact_type", "no_supported_pattern_matched")
