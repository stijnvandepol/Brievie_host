"""The measurement's verdict (P1) and the plan reading that names the slow node. No database."""

from tests.perf.report import (
    ACTORS,
    LISTS,
    STATES,
    Measurement,
    Report,
    dominant,
    render,
)


def _report(seconds: float = 0.1, *, preview: float = 1.0, gin: bool | None = True) -> Report:
    report = Report(preview_runs=[preview] * 5, preview_letters=1234, gin_index_used=gin)
    for state in STATES:
        for actor in ACTORS:
            for name in LISTS:
                report.measurements.append(
                    Measurement(state, actor, name, "policy", 0.3, [seconds] * 5, "200 rows")
                )
    return report


def _replace(report: Report, key: tuple[str, str, str], runs: list[float]) -> None:
    for m in report.measurements:
        if (m.state, m.actor, m.name) == key and m.variant == "policy":
            m.runs = runs
            m.dominant = "query 1 (400 ms): Seq Scan relation=documents_document 380 ms"


def test_everything_within_the_threshold_passes() -> None:
    text, failures = render(_report())
    assert failures == []
    assert "**VERDICT: PASS**" in text
    assert text.count("| PASS |") == len(STATES) * len(ACTORS) * len(LISTS)
    assert f"Lists: {len(STATES) * len(ACTORS) * len(LISTS)} PASS." in text
    assert "1234 letters change viewers" in text


def test_a_slow_list_fails_with_how_much_and_the_dominant_node() -> None:
    report = _report()
    _replace(report, ("own", "plain_member", "list_documents"), [0.4, 0.41, 0.39, 0.42, 0.5])
    text, failures = render(report)
    assert failures == [
        "own plain_member list_documents: 410 ms at best, 160 ms over (1.64x);"
        " query 1 (400 ms): Seq Scan relation=documents_document 380 ms"
    ]
    assert "**VERDICT: FAIL**" in text
    # median 410, p95 of five runs by nearest rank is the slowest: 500.
    assert "| own | plain_member | list_documents | 300 | 410 | 500 |" in text


def test_the_threshold_itself_passes() -> None:
    report = _report(0.250)
    assert render(report)[1] == []


def test_a_list_passes_when_its_member_filter_does() -> None:
    report = _report()
    key = ("own", "plain_member", "inbox_counts")
    _replace(report, key, [0.6] * 5)
    report.measurements.append(Measurement(*key, "member filter", 0.1, [0.05] * 5, "{}"))
    text, failures = render(report)
    assert failures == []
    assert "| own | plain_member | inbox_counts | 300 | 600 | 600 |  | 50 |" in text
    assert "PASS with member filter" in text


def test_a_list_passes_with_its_candidate_and_shows_jit_on_beside_it() -> None:
    report = _report()
    key = ("E", "admin", "list_persons")
    _replace(report, key, [0.8] * 5)
    report.measurements.append(Measurement(*key, "candidate", 0.1, [0.09] * 5, "55 rows"))
    report.measurements.append(Measurement(*key, "jit on", 0.1, [1.1] * 5, "55 rows"))
    text, failures = render(report)
    assert failures == []
    assert "| E | admin | list_persons | 300 | 800 | 800 | 90 |  | 1100 |" in text
    assert "PASS with candidate" in text


def test_a_candidate_with_another_result_fails() -> None:
    report = _report()
    report.mismatches.append("candidate own admin list_persons: a != b")
    _, failures = render(report)
    assert failures == ["different result: candidate own admin list_persons: a != b"]


def test_a_missing_measurement_fails() -> None:
    report = _report()
    report.measurements.pop()
    _, failures = render(report)
    assert failures == [f"own admin {LISTS[-1]}: not measured"]


def test_a_slow_search_is_reported_but_does_not_fail() -> None:
    """Search is measured on the same world (it reads through the same seam) but is not
    one of the lists of P1: over the threshold it is shown, not failed."""
    report = _report()
    _replace(report, ("E", "admin", "search_sender"), [0.4] * 5)
    text, failures = render(report)
    assert failures == []
    assert "| E | admin | search_sender | 300 | 400 | 400 |" in text
    assert "over, not gated" in text
    assert "**VERDICT: PASS**" in text


def test_a_slow_preview_fails() -> None:
    _, failures = render(_report(preview=2.5))
    assert failures == ["preview count: 2500 ms, 500 ms over"]


def test_the_gin_index_is_recorded_not_required() -> None:
    text, failures = render(_report(gin=False))
    assert failures == []
    assert "GIN index (EXPLAIN of inbox_counts, own, plain member): no" in text


def test_the_first_preview_design_is_reported_beside_the_grouped_one() -> None:
    report = _report()
    report.preview_per_letter = (97.6, 90_000)
    text, _ = render(report)
    assert "first design (a row per letter and member, once): 90000 letters, 97600 ms" in text


def test_dominant_node_is_the_one_with_most_time_of_its_own() -> None:
    plan = {
        "Plan": {
            "Node Type": "Limit",
            "Actual Total Time": 120.0,
            "Actual Loops": 1,
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Index Name": "documents_document_recent",
                    "Relation Name": "documents_document",
                    "Actual Total Time": 110.0,
                    "Actual Loops": 1,
                    "Plans": [
                        {
                            "Node Type": "Index Scan",
                            "Parent Relationship": "SubPlan",
                            "Relation Name": "documents_documentreader",
                            "Actual Total Time": 0.004,
                            "Actual Loops": 20000,
                        }
                    ],
                }
            ],
        },
        "Execution Time": 121.0,
    }
    own, name = dominant(plan)
    # The reader probe: 0.004 ms x 20,000 loops = 80 ms; the scan itself 30 ms.
    assert round(own) == 80
    assert name == "Index Scan relation=documents_documentreader parent=SubPlan"


def test_jit_counts_as_a_node() -> None:
    plan = {
        "Plan": {"Node Type": "Seq Scan", "Actual Total Time": 40.0, "Actual Loops": 1},
        "JIT": {"Timing": {"Total": 95.0}},
    }
    assert dominant(plan) == (95.0, "JIT compilation")
