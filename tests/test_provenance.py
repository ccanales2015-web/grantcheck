"""Tests for the GrantCheck provenance resolver.

Real fixtures go through actual document intake and replay loading. Expected
offsets, line numbers, and hashes are computed from the ingested content, so
the tests hold whether git checked the files out with LF or CRLF line endings.
"""
import bisect
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from grantcheck.document_intake import DOCUMENT_ROLES, intake_application  # noqa: E402
from grantcheck.fact_schema import FACT_SOURCES  # noqa: E402
from grantcheck.provenance import (  # noqa: E402
    MAX_LOCATIONS,
    ProvenanceError,
    find_occurrences,
    resolve_provenance,
)
from grantcheck.replay_loader import load_replay_facts  # noqa: E402

SAMPLE_DATA_DIR = ROOT / "sample_data"
SCENARIO_DIRS = sorted(path for path in SAMPLE_DATA_DIR.glob("S0*_*") if path.is_dir())
ROLE_FILENAMES = {
    "budget": "budget.txt",
    "budget_justification": "budget_justification.txt",
    "project_narrative": "project_narrative.txt",
}
RECORD_KEYS = {"filename", "document_sha256", "occurrence_count", "locations"}
LOCATION_KEYS = {"start_offset", "end_offset", "start_line", "end_line"}


# --- Helpers -----------------------------------------------------------------


def scenario_dir_for(prefix):
    matches = [path for path in SCENARIO_DIRS if path.name.startswith(prefix)]
    assert len(matches) == 1, f"expected one scenario folder for {prefix}"
    return matches[0]


def load_scenario(scenario_dir):
    """Real intake plus validated replay facts for one scenario."""
    application_id = json.loads((scenario_dir / "expected_results.json").read_text(encoding="utf-8"))["application_id"]
    application = intake_application(application_id, *(scenario_dir / ROLE_FILENAMES[role] for role in DOCUMENT_ROLES))
    assert application["intake_status"] == "completed", f"test setup: {scenario_dir.name} intake failed"
    return application, load_replay_facts(application, scenario_dir / "replay_facts.json")


def line_of(text, offset):
    """Independent oracle: 1-based line containing offset, from a table of line starts."""
    line_starts = [0] + [index + 1 for index, char in enumerate(text) if char == "\n"]
    return bisect.bisect_right(line_starts, offset)


def brute_force_starts(text, excerpt):
    """Independent oracle: every start offset where excerpt occurs, overlaps included."""
    return [i for i in range(len(text) - len(excerpt) + 1) if text[i:i + len(excerpt)] == excerpt]


def assert_record_matches_text(record, text, excerpt):
    """Every reported location is exact and agrees with the independent oracles."""
    assert set(record) == RECORD_KEYS
    starts = brute_force_starts(text, excerpt)
    assert record["occurrence_count"] == len(starts)
    assert [loc["start_offset"] for loc in record["locations"]] == starts[:MAX_LOCATIONS]
    for location in record["locations"]:
        assert set(location) == LOCATION_KEYS
        start, end = location["start_offset"], location["end_offset"]
        assert text[start:end] == excerpt
        assert location["start_line"] == line_of(text, start)
        assert location["end_line"] == line_of(text, end - 1)


def write_documents(folder, newline):
    """Write the S01 documents with exactly the given line ending, as bytes."""
    folder.mkdir()
    paths = []
    for role in DOCUMENT_ROLES:
        raw = (scenario_dir_for("S01_") / ROLE_FILENAMES[role]).read_bytes().replace(b"\r\n", b"\n")
        if newline == "\r\n":
            raw = raw.replace(b"\n", b"\r\n")
        path = folder / ROLE_FILENAMES[role]
        path.write_bytes(raw)
        paths.append(path)
    return paths


# --- find_occurrences ----------------------------------------------------------


def test_unique_excerpt():
    assert find_occurrences("alpha beta gamma", "beta") == {
        "occurrence_count": 1,
        "locations": [{"start_offset": 6, "end_offset": 10, "start_line": 1, "end_line": 1}],
    }


def test_repeated_excerpt_lists_every_occurrence_in_order():
    result = find_occurrences("ab ab\nab", "ab")
    assert result["occurrence_count"] == 3
    assert [(loc["start_offset"], loc["start_line"]) for loc in result["locations"]] == [(0, 1), (3, 1), (6, 2)]


def test_overlapping_occurrences_are_counted():
    result = find_occurrences("aaaa", "aa")
    assert result["occurrence_count"] == 3
    assert [(loc["start_offset"], loc["end_offset"]) for loc in result["locations"]] == [(0, 2), (1, 3), (2, 4)]


def test_absent_excerpt():
    assert find_occurrences("alpha", "beta") == {"occurrence_count": 0, "locations": []}


def test_more_than_max_locations_keeps_exact_count():
    text = "x\n" * 250
    result = find_occurrences(text, "x")

    assert result["occurrence_count"] == 250
    assert len(result["locations"]) == MAX_LOCATIONS == 100
    assert [loc["start_offset"] for loc in result["locations"]] == list(range(0, 200, 2))
    assert result["locations"][-1]["start_line"] == 100


def test_overlapping_count_beyond_limit_is_exact():
    result = find_occurrences("a" * 1000, "aa")
    assert result["occurrence_count"] == 999
    assert len(result["locations"]) == 100


def test_multiline_excerpt_spans_lines():
    text = "line one\nline two\nline three"
    result = find_occurrences(text, "one\nline two")
    assert result["locations"] == [{"start_offset": 5, "end_offset": 17, "start_line": 1, "end_line": 2}]


def test_excerpt_ending_in_newline_ends_on_that_line():
    # The final character is the "\n" that terminates line 1.
    result = find_occurrences("first\nsecond", "first\n")
    assert result["locations"][0]["end_line"] == 1


def test_crlf_and_lf_offsets_differ_and_do_not_cross_match():
    lf, crlf = "a\nb\nc", "a\r\nb\r\nc"

    assert find_occurrences(lf, "c")["locations"][0] == {"start_offset": 4, "end_offset": 5, "start_line": 3, "end_line": 3}
    assert find_occurrences(crlf, "c")["locations"][0] == {"start_offset": 6, "end_offset": 7, "start_line": 3, "end_line": 3}
    assert find_occurrences(crlf, "a\r\nb")["locations"][0] == {"start_offset": 0, "end_offset": 4, "start_line": 1, "end_line": 2}
    assert find_occurrences(crlf, "a\nb")["occurrence_count"] == 0
    assert find_occurrences(lf, "a\r\nb")["occurrence_count"] == 0


def test_lone_carriage_return_is_not_a_line_break():
    assert find_occurrences("a\rb", "b")["locations"][0]["start_line"] == 1


def test_offsets_are_unicode_code_points_not_bytes():
    emoji = chr(0x1F600)             # 1 code point, 4 bytes in UTF-8, 2 units in UTF-16
    accented = "caf" + chr(0xE9)     # 4 code points, 5 bytes in UTF-8
    text = f"{emoji} {accented} target"
    result = find_occurrences(text, "target")

    assert result["locations"][0]["start_offset"] == 7
    assert text.encode("utf-8").find(b"target") == 11, "the byte offset would differ"
    assert find_occurrences(text, accented)["locations"][0]["end_offset"] == 6


def test_case_sensitive_matching():
    assert find_occurrences("Personnel Cost", "personnel cost")["occurrence_count"] == 0


@pytest.mark.parametrize("text, excerpt, error", [
    ("text", "", ValueError),
    (None, "x", TypeError),
    ("text", None, TypeError),
    (b"text", "t", TypeError),
], ids=["empty_excerpt", "text_none", "excerpt_none", "bytes_text"])
def test_find_occurrences_rejects_invalid_arguments(text, excerpt, error):
    with pytest.raises(error):
        find_occurrences(text, excerpt)


# --- resolve_provenance on the seven fixtures ----------------------------------


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=[d.name for d in SCENARIO_DIRS])
def test_fixture_provenance_mirrors_schema_and_matches_text(scenario_dir):
    application, candidate_facts = load_scenario(scenario_dir)

    provenance = resolve_provenance(application, candidate_facts)

    assert list(provenance) == list(FACT_SOURCES)
    for category, roles in FACT_SOURCES.items():
        assert list(provenance[category]) == list(roles)
        for role in roles:
            record = provenance[category][role]
            source_bytes = (scenario_dir / ROLE_FILENAMES[role]).read_bytes()
            assert record["filename"] == ROLE_FILENAMES[role]
            assert record["document_sha256"] == hashlib.sha256(source_bytes).hexdigest()

            evidence = candidate_facts[category][role]["evidence_text"]
            if evidence is None:
                assert record["occurrence_count"] == 0
                assert record["locations"] == []
            else:
                assert_record_matches_text(record, application["documents"][role]["text"], evidence)
                assert record["occurrence_count"] >= 1


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=[d.name for d in SCENARIO_DIRS])
def test_fixture_excerpts_are_unique(scenario_dir):
    provenance = resolve_provenance(*load_scenario(scenario_dir))
    for category, roles in FACT_SOURCES.items():
        for role in roles:
            assert provenance[category][role]["occurrence_count"] in (0, 1), f"{category}.{role} is ambiguous"


def test_s02_personnel_cost_conflict_locations():
    application, candidate_facts = load_scenario(scenario_dir_for("S02_"))
    cost = resolve_provenance(application, candidate_facts)["personnel_cost_usd"]

    budget_text = application["documents"]["budget"]["text"]
    justification_text = application["documents"]["budget_justification"]["text"]
    # Line numbers count "\n" only, so they are the same for LF and CRLF checkouts.
    expected = {
        "budget": ("Personnel Cost: $24,000", budget_text, 12),
        "budget_justification": ("Research Assistant is $20,000.", justification_text, 16),
    }
    for role, (excerpt, text, line) in expected.items():
        start = text.index(excerpt)
        assert cost[role]["occurrence_count"] == 1
        assert cost[role]["locations"] == [{
            "start_offset": start, "end_offset": start + len(excerpt), "start_line": line, "end_line": line,
        }]


def test_s06_uncertain_travel_justification_location():
    application, candidate_facts = load_scenario(scenario_dir_for("S06_"))
    provenance = resolve_provenance(application, candidate_facts)
    text = application["documents"]["budget_justification"]["text"]

    excerpt = "Travel funds support project-related attendance and transportation activities."
    assert candidate_facts["travel_justification"]["budget_justification"]["status"] == "uncertain"
    start = text.index(excerpt)
    assert provenance["travel_justification"]["budget_justification"]["locations"] == [{
        "start_offset": start, "end_offset": start + len(excerpt), "start_line": 21, "end_line": 21,
    }]
    assert provenance["travel_cost_usd"]["budget_justification"]["locations"][0]["start_line"] == 19


def test_s07_unprocessable_records_have_no_locations():
    scenario_dir = scenario_dir_for("S07_")
    application, candidate_facts = load_scenario(scenario_dir)
    provenance = resolve_provenance(application, candidate_facts)
    placeholder_hash = hashlib.sha256((scenario_dir / "budget_justification.txt").read_bytes()).hexdigest()

    for category, roles in FACT_SOURCES.items():
        if "budget_justification" in roles:
            record = provenance[category]["budget_justification"]
            assert record == {
                "filename": "budget_justification.txt",
                "document_sha256": placeholder_hash,
                "occurrence_count": 0,
                "locations": [],
            }, f"S07 {category}.budget_justification must not have fabricated locations"
    assert provenance["personnel_cost_usd"]["budget"]["occurrence_count"] == 1


def test_s05_missing_records_keep_document_identity():
    application, candidate_facts = load_scenario(scenario_dir_for("S05_"))
    record = resolve_provenance(application, candidate_facts)["travel_justification"]["budget_justification"]

    assert record["filename"] == "budget_justification.txt"
    assert record["document_sha256"] == application["documents"]["budget_justification"]["sha256"]
    assert (record["occurrence_count"], record["locations"]) == (0, [])


# --- LF/CRLF through real intake -------------------------------------------------


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_multiline_excerpt_through_intake(tmp_path, newline):
    application = intake_application("GC-S01", *write_documents(tmp_path / "docs", newline))
    _, candidate_facts = load_scenario(scenario_dir_for("S01_"))
    candidate_facts = copy.deepcopy(candidate_facts)
    excerpt = newline.join(["The total requested personnel cost for the Graduate", "Research Assistant is $24,000."])
    candidate_facts["personnel_cost_usd"]["budget_justification"]["evidence_text"] = excerpt

    record = resolve_provenance(application, candidate_facts)["personnel_cost_usd"]["budget_justification"]
    text = application["documents"]["budget_justification"]["text"]

    assert record["occurrence_count"] == 1
    location = record["locations"][0]
    assert (location["start_line"], location["end_line"]) == (15, 16)
    assert location["start_offset"] == text.index(excerpt)
    assert text[location["start_offset"]:location["end_offset"]] == excerpt
    assert ("\r\n" in text) == (newline == "\r\n"), "test setup: wrong line endings"


def test_lf_and_crlf_documents_get_different_hashes_and_offsets(tmp_path):
    _, candidate_facts = load_scenario(scenario_dir_for("S01_"))
    lf_app = intake_application("GC-S01", *write_documents(tmp_path / "lf", "\n"))
    crlf_app = intake_application("GC-S01", *write_documents(tmp_path / "crlf", "\r\n"))

    lf = resolve_provenance(lf_app, candidate_facts)["personnel_cost_usd"]["budget"]
    crlf = resolve_provenance(crlf_app, candidate_facts)["personnel_cost_usd"]["budget"]

    assert lf["document_sha256"] != crlf["document_sha256"]
    assert lf["locations"][0]["start_line"] == crlf["locations"][0]["start_line"] == 12
    assert crlf["locations"][0]["start_offset"] - lf["locations"][0]["start_offset"] == 11  # 11 preceding "\r"


# --- Read-only behaviour and determinism -----------------------------------------


def test_inputs_are_not_mutated_and_result_is_independent():
    application, candidate_facts = load_scenario(scenario_dir_for("S02_"))
    application_before = copy.deepcopy(application)
    facts_before = copy.deepcopy(candidate_facts)

    provenance = resolve_provenance(application, candidate_facts)
    provenance["personnel_cost_usd"]["budget"]["locations"].clear()

    assert application == application_before
    assert candidate_facts == facts_before
    assert "provenance" not in application


def test_resolution_is_deterministic():
    application, candidate_facts = load_scenario(scenario_dir_for("S04_"))
    assert resolve_provenance(application, candidate_facts) == resolve_provenance(application, candidate_facts)


def test_provenance_records_do_not_duplicate_evidence_text():
    provenance = resolve_provenance(*load_scenario(scenario_dir_for("S01_")))
    for roles in provenance.values():
        for record in roles.values():
            assert "evidence_text" not in record


# --- Explicit failure on invalid input ------------------------------------------


@pytest.fixture
def s01():
    application, candidate_facts = load_scenario(scenario_dir_for("S01_"))
    return copy.deepcopy(application), copy.deepcopy(candidate_facts)


def test_excerpt_absent_from_document_fails(s01):
    application, candidate_facts = s01
    candidate_facts["personnel_role"]["budget"]["evidence_text"] = "Not in the budget"
    with pytest.raises(ProvenanceError, match=r"candidate_facts\.personnel_role\.budget\.evidence_text"):
        resolve_provenance(application, candidate_facts)


def test_excerpt_from_wrong_document_fails(s01):
    application, candidate_facts = s01
    candidate_facts["personnel_cost_usd"]["budget_justification"]["evidence_text"] = "Personnel Cost: $24,000"
    with pytest.raises(ProvenanceError, match="does not occur in the budget_justification document"):
        resolve_provenance(application, candidate_facts)


def test_invalid_candidate_facts_fail(s01):
    application, candidate_facts = s01
    del candidate_facts["travel_cost_usd"]
    with pytest.raises(ProvenanceError, match="candidate facts are not valid"):
        resolve_provenance(application, candidate_facts)


def test_evidence_on_missing_record_fails(s01):
    # The validated contract forbids evidence on missing records.
    application, candidate_facts = s01
    candidate_facts["travel_cost_usd"]["budget"].update(status="missing", value=None)
    with pytest.raises(ProvenanceError, match="candidate facts are not valid"):
        resolve_provenance(application, candidate_facts)


def change_document(role, key, value):
    def edit(application):
        if key is None:
            application["documents"][role] = value
        elif value is DELETE:
            del application["documents"][role][key]
        else:
            application["documents"][role][key] = value
    return edit


DELETE = object()

INVALID_APPLICATION_CASES = [
    ("failed_document", change_document("budget", None, None), "documents['budget']"),
    ("missing_role", lambda app: app["documents"].pop("project_narrative"), "documents['project_narrative']"),
    ("missing_filename", change_document("budget", "filename", DELETE), "filename"),
    ("blank_filename", change_document("budget", "filename", "  "), "filename"),
    ("text_not_string", change_document("budget", "text", b"bytes"), "text"),
    ("missing_sha256", change_document("budget", "sha256", DELETE), "sha256"),
    ("sha256_mismatch", change_document("budget", "sha256", "0" * 64), "sha256 does not match"),
    ("text_altered", change_document("budget", "text", "Personnel Cost: $24,000"), "sha256 does not match"),
]


@pytest.mark.parametrize(
    "edit, fragment",
    [(edit, fragment) for _, edit, fragment in INVALID_APPLICATION_CASES],
    ids=[case_id for case_id, _, _ in INVALID_APPLICATION_CASES],
)
def test_invalid_application_documents_fail(s01, edit, fragment):
    application, candidate_facts = s01
    edit(application)
    with pytest.raises(ProvenanceError, match=re.escape(fragment)):
        resolve_provenance(application, candidate_facts)


def test_basename_filename_is_accepted(s01):
    application, candidate_facts = s01
    application["documents"]["budget"]["filename"] = "budget.txt"

    record = resolve_provenance(application, candidate_facts)["personnel_cost_usd"]["budget"]

    assert record["filename"] == "budget.txt"


@pytest.mark.parametrize(
    "filename",
    [
        "documents/budget.txt",
        "documents\\budget.txt",
        "/home/reviewer/budget.txt",
        "C:\\Users\\reviewer\\budget.txt",
    ],
    ids=["relative_posix", "relative_windows", "absolute_posix", "absolute_windows"],
)
def test_filename_with_directory_components_fails(s01, filename):
    application, candidate_facts = s01
    application["documents"]["budget"]["filename"] = filename

    with pytest.raises(ProvenanceError, match=re.escape(
        "application documents['budget']['filename'] must be a file name without directory components"
    )):
        resolve_provenance(application, candidate_facts)


@pytest.mark.parametrize("application", [None, [], "app", {"documents": []}, {}], ids=["none", "list", "string", "documents_list", "no_documents"])
def test_invalid_application_object_fails(s01, application):
    _, candidate_facts = s01
    with pytest.raises(ProvenanceError):
        resolve_provenance(application, candidate_facts)
