"""The measurement's report: medians, the dominant plan node, the verdict of P1.

Plain Python, tested without a database (tests/perf/test_report.py); the measurement
itself is tests/perf/test_visibility_at_scale.py.
"""

import math
import statistics
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final

THRESHOLD: Final = 0.250  # seconds, median per list (S 4.9.5, P1)
PREVIEW_THRESHOLD: Final = 2.0  # seconds, the preview's letter count (P1)
RUNS: Final = 5

STATES: Final = {
    "E": {"everyone_sees_all": True, "departments_enabled": False, "admins_read_all": True},
    "own+D": {"everyone_sees_all": False, "departments_enabled": True, "admins_read_all": False},
    "own": {"everyone_sees_all": False, "departments_enabled": False, "admins_read_all": False},
}
ACTORS: Final = ("plain_member", "department_member", "sorter", "admin")
LISTS: Final = (
    "list_documents",
    "list_documents_unassigned",
    "list_documents_own_person",
    "inbox_counts",
    "list_persons",
    "list_labels",
    "list_tasks_all",
    # Mijn taken (spec S 11): the member's list, the organisation's second part, the counts.
    "list_my_tasks",
    "list_my_tasks_to_pick_up",
    "count_my_tasks",
    # Search (merged with M1): through the same seam, so measured on the same world.
    "search_sender",
    "search_common_word",
)
# Measured and shown, not part of P1's verdict (search is not a list of S 4.9.5); their
# results must still match without the member filter.
NOT_GATED: Final = frozenset({"search_sender", "search_common_word"})


@dataclass
class Measurement:
    state: str
    actor: str
    name: str
    variant: str  # "policy", "candidate", "member filter" or "jit on"
    first: float
    runs: list[float]
    result: str  # rows, or the counts
    dominant: str = ""
    explain_file: str = ""

    @property
    def median(self) -> float:
        return statistics.median(self.runs)

    @property
    def p95(self) -> float:
        ordered = sorted(self.runs)
        return ordered[math.ceil(0.95 * len(ordered)) - 1]


@dataclass
class Report:
    measurements: list[Measurement] = field(default_factory=list)
    preview_runs: list[float] = field(default_factory=list)
    preview_letters: int = -1
    mismatches: list[str] = field(default_factory=list)
    gin_index_used: bool | None = None
    preview_per_letter: tuple[float, int] | None = None
    server: dict[str, str] = field(default_factory=dict)


def children(node: dict[str, object]) -> list[dict[str, object]]:
    plans = node.get("Plans", [])
    return [p for p in plans if isinstance(p, dict)] if isinstance(plans, list) else []


def number(node: dict[str, object], key: str) -> float:
    value = node.get(key, 0)
    return float(value) if isinstance(value, int | float) else 0.0


def own_times(node: dict[str, object]) -> Iterator[tuple[float, str]]:
    """(milliseconds of the node's own, a name for it) for every node of a plan."""
    loops = number(node, "Actual Loops") or 1.0
    total = number(node, "Actual Total Time") * loops
    below = sum(
        number(child, "Actual Total Time") * (number(child, "Actual Loops") or 1.0)
        for child in children(node)
    )
    name = str(node.get("Node Type", "?"))
    for key in ("Relation Name", "Index Name", "Parent Relationship"):
        if key in node:
            name += f" {key.split()[0].lower()}={node[key]}"
    yield max(total - below, 0.0), name
    for child in children(node):
        yield from own_times(child)


def dominant(top: dict[str, object]) -> tuple[float, str]:
    plan = top.get("Plan")
    assert isinstance(plan, dict)
    candidates = list(own_times(plan))
    jit = top.get("JIT")
    if isinstance(jit, dict) and isinstance(jit.get("Timing"), dict):
        timing = jit["Timing"]
        candidates.append((number(timing, "Total"), "JIT compilation"))
    return max(candidates)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f}"


def _verdict(service: float, candidate: float | None, filtered: float | None) -> str:
    if service <= THRESHOLD:
        return "PASS"
    if candidate is not None and candidate <= THRESHOLD:
        return "PASS with candidate"
    if filtered is not None and filtered <= THRESHOLD:
        return "PASS with member filter"
    return "FAIL"


def render(report: Report) -> tuple[str, list[str]]:
    """The markdown report and the reasons it fails (empty when it passes)."""
    failures: list[str] = []
    by: dict[str, dict[tuple[str, str, str], Measurement]] = {}
    for item in report.measurements:
        by.setdefault(item.variant, {})[(item.state, item.actor, item.name)] = item
    policy = by.get("policy", {})
    lines = [
        "# Visibility at 100,000 letters",
        "",
        f"Threshold: median <= {_ms(THRESHOLD)} ms per list, preview count <="
        f" {_ms(PREVIEW_THRESHOLD)} ms. Measured with jit=off, the production setting;"
        ' "jit on" is the same service with PostgreSQL\'s default, for comparison. A list'
        " passes on the service as it ships (with the member filter where it applies;"
        " the same result without it required). "
        f"{RUNS} timed runs after one first run; p95 by nearest rank (over {RUNS} runs: the"
        ' slowest). "first" is the first run of that query in the measurement: a new plan,'
        " but the server's and the machine's caches are not emptied.",
        "",
        *(f"- {key}: {value}" for key, value in report.server.items()),
        "",
        "| state | actor | list | first ms | median ms | p95 ms | candidate ms"
        " | member filter ms | jit on ms | result | verdict | slowest query: dominant node |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    verdicts: dict[str, int] = {}
    for state in STATES:
        for actor in ACTORS:
            for name in LISTS:
                key = (state, actor, name)
                m = policy.get(key)
                if m is None:
                    failures.append(f"{state} {actor} {name}: not measured")
                    lines.append(f"| {state} | {actor} | {name} | | | | | | | | NOT MEASURED | |")
                    continue
                c = by.get("candidate", {}).get(key)
                f = by.get("member filter", {}).get(key)
                j = by.get("jit on", {}).get(key)
                verdict = _verdict(m.median, c.median if c else None, f.median if f else None)
                if name in NOT_GATED and verdict == "FAIL":
                    verdict = "over, not gated"
                verdicts[verdict] = verdicts.get(verdict, 0) + 1
                where = m.dominant
                if c is not None:
                    where += f" / candidate: {c.dominant}"
                if verdict == "FAIL":
                    best = min(x.median for x in (m, c, f) if x is not None)
                    failures.append(
                        f"{state} {actor} {name}: {_ms(best)} ms at best,"
                        f" {_ms(best - THRESHOLD)} ms over ({best / THRESHOLD:.2f}x); {where}"
                    )
                lines.append(
                    f"| {state} | {actor} | {name} | {_ms(m.first)} | {_ms(m.median)}"
                    f" | {_ms(m.p95)} | {_ms(c.median) if c else ''}"
                    f" | {_ms(f.median) if f else ''} | {_ms(j.median) if j else ''}"
                    f" | {m.result[:40]} | {verdict} | {where} |"
                )
    lines.append("")
    lines.append(
        "Lists: " + ", ".join(f"{n} {v}" for v, n in sorted(verdicts.items())) + "."
        if verdicts
        else "Lists: none measured."
    )
    if report.preview_per_letter is not None:
        seconds, letters = report.preview_per_letter
        lines.append(
            f"Preview, first design (a row per letter and member, once): {letters} letters,"
            f" {_ms(seconds)} ms."
        )
    if report.preview_runs:
        median = statistics.median(report.preview_runs)
        verdict = "PASS" if median <= PREVIEW_THRESHOLD else "FAIL"
        lines.append(
            f"Preview letter count, grouped (E -> own+D, as the system):"
            f" {report.preview_letters} letters change viewers; median {_ms(median)} ms,"
            f" slowest {_ms(max(report.preview_runs))} ms: {verdict}."
        )
        if verdict == "FAIL":
            failures.append(
                f"preview count: {_ms(median)} ms, {_ms(median - PREVIEW_THRESHOLD)} ms over"
            )
    else:
        failures.append("preview count: not measured")
        lines.append("Preview letter count: NOT MEASURED.")
    lines.append(
        "Member filter uses the GIN index (EXPLAIN of inbox_counts, own, plain member): "
        + {True: "yes", False: "no", None: "not checked"}[report.gin_index_used]
        + " (recorded, not a condition)."
    )
    failures += [f"different result: {mismatch}" for mismatch in report.mismatches]
    lines.append("")
    if failures:
        lines.append(f"**VERDICT: FAIL** ({len(failures)} of the checks above):")
        lines += [f"- {reason}" for reason in failures]
    else:
        lines.append(
            "**VERDICT: PASS**: every gated list and the preview count within the threshold."
        )
    return "\n".join(lines) + "\n", failures
