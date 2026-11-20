"""The visibility check at 100,000 letters (S 4.9.5, plan Task 1, ruling P1).

Runs only with BRIEVIE_PERF=1 (CI job `visibility-perf`): building the world takes
about a minute. On the scratch schema (tests/perf/scratch_schema.py), with the policy
condition that ships, the lists are measured through the real service functions as a
request runs them: inside one transaction, as the non-superuser test role bound by the
forced policies, with `brievie.workspace_id` and `brievie.actor` set transaction-local.

Per state x actor x list: one first run (reported as "first": a new plan for that query,
but shared buffers already hold the data of earlier lists; nothing here empties the
server's or the machine's caches) and five timed runs (median, and p95 by nearest rank,
which over five runs is the slowest). Every query of one more run is explained with
ANALYZE and BUFFERS: the text goes to the artifact directory, the node with the most
time of its own is named in the report. `test_verdict` runs last and writes the report.
"""

import hashlib
import json
import os
import platform
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from django.db.models import BooleanField, Q, QuerySet
from django.db.models.expressions import RawSQL
from django.test.utils import CaptureQueriesContext
from pytest_django import DjangoDbBlocker

from apps.documents import services as documents
from apps.documents.models import Document
from apps.labels import services as labels
from apps.people import services as people
from apps.tasks import services as tasks
from apps.workspaces.models import Workspace
from apps.workspaces.visibility_sql import viewer_pairs_sql
from tests.perf import data, scratch_schema
from tests.perf.data import PerfWorld
from tests.perf.report import (
    ACTORS,
    LISTS,
    RUNS,
    STATES,
    Measurement,
    Report,
    children,
    dominant,
    number,
    render,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("BRIEVIE_PERF") != "1",
    reason="The 100,000-letter measurement runs with BRIEVIE_PERF=1 (CI job visibility-perf).",
)

# The lists the member filter applies to (S 4.9.5; ships in Task 11).
FILTERED: Final = (
    "list_documents",
    "list_documents_unassigned",
    "list_documents_own_person",
    "inbox_counts",
)

# The member filter, measured here before Task 11 builds it: a superset of what the
# policy shows a member who neither sees all nor sorts post, so the result is the same
# (checked on every run), with every arm on an index so PostgreSQL can combine them in
# a BitmapOr: the GIN index for the addressees, the foreign-key index for the scanner,
# the primary key for the reader rows. The policy still decides.
MEMBER_FILTER: Final = (
    '("documents_document"."addressee_ids" OPERATOR(pg_catalog.&&)'
    " (SELECT public.brievie_my_post_ids(%s::uuid))"
    ' OR "documents_document"."scanned_by_id" = %s::uuid'
    ' OR "documents_document"."id" = ANY (ARRAY('
    "SELECT r.document_id FROM public.documents_documentreader r WHERE r.user_id = %s::uuid)))"
)


def _report_path() -> Path | None:
    value = os.environ.get("BRIEVIE_PERF_REPORT", "")
    return Path(value) if value else None


def _explain_dir() -> Path | None:
    report = _report_path()
    if report is None:
        return None
    directory = report.parent / "perf-explain"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="module")
def perf_world(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> PerfWorld:
    with django_db_blocker.unblock():
        with transaction.atomic(), connection.cursor() as c:
            scratch_schema.install(c)
        return data.build(uuid4())


@pytest.fixture(scope="module")
def perf_report() -> Report:
    return Report()


def _set_state(world: PerfWorld, state: str) -> None:
    values = STATES[state]
    with connection.cursor() as c:
        c.execute(
            "UPDATE workspaces_workspace SET everyone_sees_all = %s, departments_enabled = %s,"
            " admins_read_all = %s WHERE id = %s",
            [
                values["everyone_sees_all"],
                values["departments_enabled"],
                values["admins_read_all"],
                world.workspace_id,
            ],
        )


def _enter(world: PerfWorld, actor: UUID | str) -> None:
    """What a request does (Task 4 makes this `workspace_context(ws, actor=…)`)."""
    with connection.cursor() as c:
        c.execute(
            "SELECT set_config('brievie.workspace_id', %s, true),"
            " set_config('brievie.actor', %s, true)",
            [str(world.workspace_id), str(actor)],
        )


@contextmanager
def _member_filter(actor: UUID) -> Iterator[None]:
    """`Document.objects.filter(…)` with the member filter added, for the services."""
    manager = Document.objects
    original = manager.filter
    # A fixed fragment; the actor's id goes in as a parameter.
    condition = RawSQL(MEMBER_FILTER, [str(actor)] * 3, output_field=BooleanField())  # noqa: S611

    def filtered(*args: Q, **kwargs: object) -> QuerySet[Document]:
        return original(*args, **kwargs).filter(condition)

    manager.filter = filtered  # type: ignore[method-assign]
    try:
        yield
    finally:
        del manager.filter


def _list(name: str, workspace: Workspace, world: PerfWorld, actor: UUID) -> Callable[[], str]:
    def ids(rows: list[Document]) -> str:
        # Every id, in order: the member filter must give exactly the same list.
        digest = hashlib.sha256(",".join(str(row.id) for row in rows).encode()).hexdigest()
        return f"{len(rows)} rows #{digest[:12]}"

    calls: dict[str, Callable[[], str]] = {
        "list_documents": lambda: ids(documents.list_documents(workspace)),
        "list_documents_unassigned": lambda: ids(
            documents.list_documents(workspace, inbox_state="unassigned")
        ),
        "list_documents_own_person": lambda: ids(
            documents.list_documents(workspace, person_id=world.own_person[actor])
        ),
        "inbox_counts": lambda: json.dumps(documents.inbox_counts(workspace), sort_keys=True),
        "list_persons": lambda: f"{len(people.list_persons(workspace))} rows",
        "list_labels": lambda: f"{len(labels.list_labels(workspace))} rows",
        "list_tasks_all": lambda: f"{len(tasks.list_tasks(workspace, status='all'))} rows",
    }
    return calls[name]


def _explain(sql: str, *, analyze: bool) -> tuple[str, dict[str, object]]:
    """The text plan and the JSON plan of one captured query (executed when analyzing)."""
    options = "ANALYZE, BUFFERS, " if analyze else ""
    with connection.cursor() as c:
        c.execute(f"EXPLAIN ({options}FORMAT TEXT) {sql}")
        text = "\n".join(str(row[0]) for row in c.fetchall())
        c.execute(f"EXPLAIN ({options}FORMAT JSON) {sql}")
        row = c.fetchone()
    assert row is not None
    raw = row[0]
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(parsed, list)
    top = parsed[0]
    assert isinstance(top, dict)
    return text, top


def _measure(call: Callable[[], str], label: str) -> tuple[float, list[float], str, str, str]:
    """first run, timed runs, result, dominant node of the slowest query, explain file."""
    start = time.perf_counter()
    result = call()
    first = time.perf_counter() - start
    runs = []
    for _ in range(RUNS):
        start = time.perf_counter()
        again = call()
        runs.append(time.perf_counter() - start)
        assert again == result
    with CaptureQueriesContext(connection) as captured:
        call()
    sections = []
    slowest: tuple[float, str] = (-1.0, "")
    for index, query in enumerate(captured.captured_queries, 1):
        text, top = _explain(query["sql"], analyze=True)
        execution = number(top, "Execution Time") + number(top, "Planning Time")
        own, node = dominant(top)
        sections.append(f"-- query {index}: {execution:.1f} ms\n{query['sql']}\n\n{text}\n")
        if execution > slowest[0]:
            slowest = (execution, f"query {index} ({execution:.0f} ms): {node} {own:.0f} ms")
    explain_file = ""
    directory = _explain_dir()
    if directory is not None:
        path = directory / f"{label}.txt"
        path.write_text("\n".join(sections))
        explain_file = path.name
    return first, runs, result, slowest[1], explain_file


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("name", LISTS)
def test_measure_list(
    state: str, actor: str, name: str, perf_world: PerfWorld, perf_report: Report
) -> None:
    user = getattr(perf_world, actor)
    assert isinstance(user, UUID)
    _set_state(perf_world, state)
    _enter(perf_world, user)
    workspace = Workspace.objects.get(id=perf_world.workspace_id)
    call = _list(name, workspace, perf_world, user)
    label = f"{state}-{actor}-{name}".replace("+", "plus")
    first, runs, result, dominant, explain_file = _measure(call, label)
    perf_report.measurements.append(
        Measurement(state, actor, name, "policy", first, runs, result, dominant, explain_file)
    )

    with connection.cursor() as c:
        c.execute(
            "SELECT public.brievie_sees_all_post(%s), public.brievie_actor_sorts_post(%s)",
            [user, user],
        )
        row = c.fetchone()
    assert row is not None
    sees_all, sorts = row
    if name not in FILTERED or sees_all or sorts:
        return
    with _member_filter(user):
        first, runs, filtered, dominant, explain_file = _measure(call, f"{label}-member-filter")
    perf_report.measurements.append(
        Measurement(
            state, actor, name, "member filter", first, runs, filtered, dominant, explain_file
        )
    )
    if filtered != result:
        perf_report.mismatches.append(f"{state} {actor} {name}: {filtered} != {result}")
    assert filtered == result


def test_measure_preview_letter_count(perf_world: PerfWorld, perf_report: Report) -> None:
    """As the system: letters whose set of viewers differs between "E" and "own+D"."""
    _enter(perf_world, "system")
    sql = (
        "SELECT id, md5(string_agg(user_id::text, ',' ORDER BY user_id)) FROM ("  # noqa: S608
        + viewer_pairs_sql("d.workspace_id = %s")
        + ") v GROUP BY id"
    )

    def viewers(state: str) -> dict[UUID, str]:
        _set_state(perf_world, state)
        with connection.cursor() as c:
            c.execute(sql, [perf_world.workspace_id])
            return {row[0]: row[1] for row in c.fetchall()}

    changed = -1
    for run in range(RUNS + 1):
        start = time.perf_counter()
        before = viewers("E")
        after = viewers("own+D")
        changed = sum(before.get(k) != after.get(k) for k in before.keys() | after.keys())
        if run:  # the first run warms up
            perf_report.preview_runs.append(time.perf_counter() - start)
    perf_report.preview_letters = changed
    assert changed > 0


def test_explain_member_filter_uses_the_gin_index(
    perf_world: PerfWorld, perf_report: Report
) -> None:
    _set_state(perf_world, "own")
    _enter(perf_world, perf_world.plain_member)
    workspace = Workspace.objects.get(id=perf_world.workspace_id)
    with _member_filter(perf_world.plain_member), CaptureQueriesContext(connection) as captured:
        documents.inbox_counts(workspace)
    (query,) = captured.captured_queries
    text, top = _explain(query["sql"], analyze=False)
    plan = top.get("Plan")
    assert isinstance(plan, dict)

    def nodes(node: dict[str, object]) -> Iterator[dict[str, object]]:
        yield node
        for child in children(node):
            yield from nodes(child)

    used = any(
        n.get("Node Type") == "Bitmap Index Scan"
        and n.get("Index Name") == "documents_document_addressees"
        for n in nodes(plan)
    )
    perf_report.gin_index_used = used
    directory = _explain_dir()
    if directory is not None:
        (directory / "own-plain_member-inbox_counts-member-filter-plan.txt").write_text(text)
    assert used, text


def _server_info() -> dict[str, str]:
    info = {
        "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs",
        # GitHub-hosted runners name their image in these two variables.
        "runner": " ".join(os.environ.get(v, "") for v in ("ImageOS", "ImageVersion")),
    }
    with connection.cursor() as c:
        c.execute("SELECT version()")
        row = c.fetchone()
        info["postgres"] = str(row[0]) if row else "?"
        for setting in (
            "shared_buffers",
            "work_mem",
            "effective_cache_size",
            "jit",
            "random_page_cost",
        ):
            c.execute(f"SHOW {setting}")
            row = c.fetchone()
            info[setting] = str(row[0]) if row else "?"
    return info


def test_verdict(perf_world: PerfWorld, perf_report: Report) -> None:
    """Runs last (file order): writes the report and applies P1."""
    perf_report.server = _server_info()
    text, failures = render(perf_report)
    path = _report_path()
    if path is not None:
        path.write_text(text)
    print(text)  # also in the job log
    assert not failures, "\n".join(failures)
