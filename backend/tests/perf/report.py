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
)


@dataclass
class Measurement:
    state: str
    actor: str
    name: str
    variant: str  # "policy" or "member filter"
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


def render(report: Report) -> tuple[str, list[str]]:
    """The markdown report and the reasons it fails (empty when it passes)."""
    failures: list[str] = []
    policy = {(m.state, m.actor, m.name): m for m in report.measurements if m.variant == "policy"}
    filtered = {
        (m.state, m.actor, m.name): m for m in report.measurements if m.variant == "member filter"
    }
    lines = [
        "# Visibility at 100,000 letters",
        "",
        f"Threshold: median <= {_ms(THRESHOLD)} ms per list (a list passes when the plain"
        f" policy or the member filter does), preview count <= {_ms(PREVIEW_THRESHOLD)} ms."
        f" {RUNS} timed runs after one first run; p95 by nearest rank (over {RUNS} runs: the"
        ' slowest). "first" is the first run of that query in the measurement: a new plan,'
        " but the server's and the machine's caches are not emptied.",
        "",
        *(f"- {key}: {value}" for key, value in report.server.items()),
        "",
        "| state | actor | list | first ms | median ms | p95 ms | member filter median ms"
        " | result | verdict | slowest query: dominant node |",
        "|---|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for state in STATES:
        for actor in ACTORS:
            for name in LISTS:
                m = policy.get((state, actor, name))
                if m is None:
                    failures.append(f"{state} {actor} {name}: not measured")
                    lines.append(f"| {state} | {actor} | {name} | | | | | | NOT MEASURED | |")
                    continue
                f = filtered.get((state, actor, name))
                best = min(m.median, f.median) if f else m.median
                verdict = "PASS" if best <= THRESHOLD else "FAIL"
                where = m.dominant
                if f is not None:
                    where += f" / with filter: {f.dominant}"
                if verdict == "FAIL":
                    over = best - THRESHOLD
                    failures.append(
                        f"{state} {actor} {name}: {_ms(best)} ms, {_ms(over)} ms over"
                        f" ({best / THRESHOLD:.2f}x); {where}"
                    )
                lines.append(
                    f"| {state} | {actor} | {name} | {_ms(m.first)} | {_ms(m.median)}"
                    f" | {_ms(m.p95)} | {_ms(f.median) if f else ''} | {m.result[:40]}"
                    f" | {verdict} | {where} |"
                )
    lines.append("")
    if report.preview_runs:
        median = statistics.median(report.preview_runs)
        verdict = "PASS" if median <= PREVIEW_THRESHOLD else "FAIL"
        lines.append(
            f"Preview letter count (E -> own+D, as the system): {report.preview_letters}"
            f" letters change viewers; median {_ms(median)} ms, slowest"
            f" {_ms(max(report.preview_runs))} ms: {verdict}."
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
        + {True: "yes", False: "NO", None: "not checked"}[report.gin_index_used]
        + "."
    )
    if report.gin_index_used is not True:
        failures.append("the member filter does not use the GIN index")
    failures += [f"member filter changed the result: {m}" for m in report.mismatches]
    lines.append("")
    if failures:
        lines.append(f"**VERDICT: FAIL** ({len(failures)} of the checks above):")
        lines += [f"- {reason}" for reason in failures]
    else:
        lines.append("**VERDICT: PASS**: every list and the preview count within the threshold.")
    return "\n".join(lines) + "\n", failures
