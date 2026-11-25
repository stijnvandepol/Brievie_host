"""The visibility check at 100,000 letters (S 4.9.5, plan Task 1, ruling P1).

Runs only with BRIEVIE_PERF=1 (CI job `visibility-perf`): building the world takes
about a minute. On the migrated schema (the policies, functions and indexes of Task 7),
the lists are measured through the real service functions as a
request runs them: inside one transaction, as the non-superuser test role bound by the
forced policies, with `brievie.workspace_id` and `brievie.actor` set transaction-local.

JIT: the app's connections run with `jit=off` (brievie/settings.py), and so does this
measurement; each list is measured once more with `SET LOCAL jit = on` (PostgreSQL's
default) so the report shows both. The services add the member filter themselves
(S 4.9.5, plan Task 11); for a filtered list the result without it must be the same.

Per state x actor x list: one first run (reported as "first": a new plan for that query,
but shared buffers already hold the data of earlier lists; nothing here empties the
server's or the machine's caches; it also warms them for this list) and nine timed runs
(median, and p95 by nearest rank, which over nine runs is the slowest); "jit on" three.
Every query of one more run is explained with ANALYZE and BUFFERS: the text goes to the
artifact directory, the node with the most time of its own is named in the report.
`test_verdict` runs last and writes the report.
"""

import hashlib
import json
import os
import platform
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pytest_django import DjangoDbBlocker

from apps.documents import lingering, search, visibility
from apps.documents import services as documents
from apps.documents.models import Document
from apps.labels import services as labels
from apps.people import services as people
from apps.tasks import services as tasks
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_sql import viewer_groups_sql, viewer_pairs_sql
from tests.perf import data
from tests.perf.data import PerfWorld
from tests.perf.report import (
    ACTORS,
    COMPARE_RUNS,
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

# The lists the member filter applies to (S 4.9.5).
FILTERED: Final = (
    "search_sender",
    "search_common_word",
    "list_documents",
    "list_documents_unassigned",
    "list_documents_own_person",
    "inbox_counts",
    "menu_counts",
    "list_my_post",
    "list_my_post_unread",
    "list_all_unread",
    "lingering_letters",
    "list_documents_open",
    "list_documents_own_person_waiting",
    "letter_status_counts",
    "letter_status_counts_own_person",
    "list_archive",
    "list_archive_kind_type",
    "list_archive_person_year",
    "archive_facets",
    "search_with_title",
    "list_archive_expiring",
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


def _say(config: pytest.Config, text: str) -> None:
    """A progress line on the terminal, past the capture: a run that is stopped (the job's
    time limit) still shows how far it came."""
    reporter = config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(text)


@pytest.fixture(scope="module")
def perf_world(
    request: pytest.FixtureRequest, django_db_setup: None, django_db_blocker: DjangoDbBlocker
) -> PerfWorld:
    started = time.perf_counter()
    with django_db_blocker.unblock():
        world = data.build(uuid4())
    _say(request.config, f"perf world built in {time.perf_counter() - started:.0f} s")
    return world


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
    """What a request does (`enter_workspace_for_transaction`); "system" for the preview."""
    with connection.cursor() as c:
        c.execute(
            "SELECT set_config('brievie.workspace_id', %s, true),"
            " set_config('brievie.actor', %s, true)",
            [str(world.workspace_id), str(actor)],
        )


def _list(name: str, workspace: Workspace, world: PerfWorld, actor: UUID) -> Callable[[], str]:
    def ids(rows: list[Document]) -> str:
        # Every id, in order: the member filter must give exactly the same list.
        digest = hashlib.sha256(",".join(str(row.id) for row in rows).encode()).hexdigest()
        return f"{len(rows)} rows #{digest[:12]}"

    def counted(rows: Sequence[object]) -> str:
        # Every row with its count: the run without the member filter must match.
        pairs = ",".join(f"{getattr(r, 'id', '')}={getattr(r, 'document_count', '')}" for r in rows)
        return f"{len(rows)} rows #{hashlib.sha256(pairs.encode()).hexdigest()[:12]}"

    def listed(result: tasks.MyTaskList) -> str:
        digest = hashlib.sha256(",".join(str(i.task.id) for i in result.items).encode())
        return f"{len(result.items)} rows #{digest.hexdigest()[:12]}"

    def lingered(result: lingering.Lingering) -> str:
        return f"{ids(result.too_long_in_inbox)} / {ids(result.not_opened)}"

    # The document lists take the member (the one visibility seam); fetched before timing.
    member = Membership.objects.select_related("workspace").get(workspace=workspace, user_id=actor)

    def found(q: str) -> list[Document]:
        result = search.search_documents(
            member, q=q, filters=search.SearchFilters(), limit=30, offset=0
        )
        return [hit.document for hit in result.hits]

    calls: dict[str, Callable[[], str]] = {
        "list_documents": lambda: ids(documents.list_documents(member)),
        "list_documents_unassigned": lambda: ids(
            documents.list_documents(member, inbox_state="unassigned")
        ),
        "list_documents_own_person": lambda: ids(
            documents.list_documents(member, person_id=world.own_person[actor])
        ),
        "inbox_counts": lambda: json.dumps(documents.inbox_counts(member), sort_keys=True),
        "list_documents_open": lambda: ids(documents.list_documents(member, letter_status="open")),
        "list_documents_own_person_waiting": lambda: ids(
            documents.list_documents(
                member, person_id=world.own_person[actor], letter_status="waiting"
            )
        ),
        "letter_status_counts": lambda: json.dumps(
            vars(documents.status_counts(member)), sort_keys=True
        ),
        "letter_status_counts_own_person": lambda: json.dumps(
            vars(documents.status_counts(member, person_id=world.own_person[actor])),
            sort_keys=True,
        ),
        "list_persons": lambda: counted(people.list_persons(workspace)),
        "list_labels": lambda: counted(labels.list_labels(workspace)),
        "list_tasks_all": lambda: f"{len(tasks.list_tasks(workspace, status='all'))} rows",
        "list_my_tasks": lambda: listed(tasks.my_tasks(member)),
        "list_my_tasks_to_pick_up": lambda: listed(tasks.my_tasks(member, part="to_pick_up")),
        "count_my_tasks": lambda: json.dumps(vars(tasks.count_my_tasks(member)), sort_keys=True),
        # A sender (one in 200 letters), and a word in every summary (ranks the newest
        # MAX_CANDIDATES). Both match exactly, so the typo fallback does not run.
        "search_sender": lambda: ids(found("afzender 042")),
        "search_common_word": lambda: ids(found("samenvatting")),
        "menu_counts": lambda: json.dumps(vars(documents.menu_counts(member)), sort_keys=True),
        "list_my_post": lambda: ids(documents.list_documents(member, addressed_to_me=True)),
        "list_my_post_unread": lambda: ids(
            documents.list_documents(member, addressed_to_me=True, unread_only=True)
        ),
        "list_all_unread": lambda: ids(documents.list_documents(member, unread_only=True)),
        "lingering_letters": lambda: lingered(lingering.lingering_letters(member)),
        "list_archive": lambda: ids(documents.list_documents(member, archive=ARCHIVED)),
        "list_archive_kind_type": lambda: ids(
            documents.list_documents(
                member,
                archive=documents.ArchiveFilters(
                    archived=True, kind="document", document_type="contract"
                ),
            )
        ),
        "list_archive_person_year": lambda: ids(
            documents.list_documents(
                member,
                person_id=world.own_person[actor],
                archive=documents.ArchiveFilters(archived=True, year=_year_back(2)),
            )
        ),
        "archive_facets": lambda: json.dumps(
            asdict(documents.list_archive_facets(member)), sort_keys=True
        ),
        "search_with_title": lambda: ids(found("loonstrook wasmachine")),
        "list_archive_expiring": lambda: ids(
            documents.list_documents(
                member, archive=documents.ArchiveFilters(archived=True, expiring=True)
            )
        ),
    }
    return calls[name]


ARCHIVED: Final = documents.ArchiveFilters(archived=True)


def _year_back(years: int) -> int:
    return date.today().year - years


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


def _measure(
    call: Callable[[], str], label: str, *, explain: bool = True, runs: int = RUNS
) -> tuple[float, list[float], str, str, str]:
    """first run, timed runs, result, dominant node of the slowest query, explain file."""
    start = time.perf_counter()
    result = call()
    first = time.perf_counter() - start
    timed = []
    for _ in range(runs):
        start = time.perf_counter()
        again = call()
        timed.append(time.perf_counter() - start)
        assert again == result
    if not explain:
        return first, timed, result, "", ""
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
    return first, timed, result, slowest[1], explain_file


def _jit() -> str:
    with connection.cursor() as c:
        c.execute("SHOW jit")
        row = c.fetchone()
    return str(row[0]) if row else "?"


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("actor", ACTORS)
@pytest.mark.parametrize("name", LISTS)
def test_measure_list(
    request: pytest.FixtureRequest,
    state: str,
    actor: str,
    name: str,
    perf_world: PerfWorld,
    perf_report: Report,
) -> None:
    user = getattr(perf_world, actor)
    assert isinstance(user, UUID)
    _set_state(perf_world, state)
    _enter(perf_world, user)
    # The production setting (brievie/settings.py), not one chosen here.
    assert _jit() == "off"
    workspace = Workspace.objects.get(id=perf_world.workspace_id)
    call = _list(name, workspace, perf_world, user)
    assert call is not None
    label = f"{state}-{actor}-{name}".replace("+", "plus")

    def record(variant: str, measured: tuple[float, list[float], str, str, str]) -> str:
        first, runs, result, node, explain_file = measured
        perf_report.measurements.append(
            Measurement(state, actor, name, variant, first, runs, result, node, explain_file)
        )
        return result

    result = record("policy", _measure(call, label))
    median = perf_report.measurements[-1].median
    _say(request.config, f"{label}: median {median * 1000:.0f} ms")

    with connection.cursor() as c:
        c.execute(
            "SELECT public.brievie_sees_all_post(%s), public.brievie_actor_sorts_post(%s)",
            [user, user],
        )
        row = c.fetchone()
    assert row is not None
    sees_all, sorts = row
    if name in FILTERED and not sees_all and not sorts:
        # The services as before Task 11: the policy alone.
        with pytest.MonkeyPatch.context() as m:
            m.setattr(visibility, "member_filter", lambda: None)
            unfiltered = call()
        if unfiltered != result:
            perf_report.mismatches.append(
                f"filter {state} {actor} {name}: {result} != {unfiltered} without it"
            )

    # PostgreSQL's default, for the comparison only.
    with connection.cursor() as c:
        c.execute("SET LOCAL jit = on")
    record("jit on", _measure(call, f"{label}-jit-on", explain=False, runs=COMPARE_RUNS))
    with connection.cursor() as c:
        c.execute("SET LOCAL jit = off")
    assert not perf_report.mismatches


def _per_letter(world: PerfWorld, state: str, document_filter: str) -> dict[UUID, str]:
    """Letter -> a digest of its viewers, one row per letter and member (the first design)."""
    _set_state(world, state)
    sql = (
        "SELECT id, md5(string_agg(user_id::text, ',' ORDER BY user_id)) FROM ("  # noqa: S608
        + viewer_pairs_sql(document_filter)
        + ") v GROUP BY id"
    )
    with connection.cursor() as c:
        c.execute(sql, [world.workspace_id])
        return {row[0]: row[1] for row in c.fetchall()}


def _grouped(world: PerfWorld, state: str) -> dict[tuple[object, ...], tuple[int, list[UUID]]]:
    """(addressees, scanner, status) -> (letters, viewers): one row per group of alike letters."""
    _set_state(world, state)
    with connection.cursor() as c:
        c.execute(viewer_groups_sql("d.workspace_id = %s"), [world.workspace_id])
        return {
            (tuple(row[0]), row[1], row[2]): (int(row[3]), list(row[4])) for row in c.fetchall()
        }


# Non-private letters with a reader row: asked letter by letter (few).
_WITH_READERS: Final = (
    "d.workspace_id = %s AND d.visibility = 'workspace' AND EXISTS (SELECT 1"
    " FROM public.documents_documentreader r WHERE r.document_id = d.id"
    " AND (r.expires_at IS NULL OR r.expires_at > pg_catalog.now()))"
)


def preview_count(world: PerfWorld, before: str, after: str) -> int:
    """Letters whose viewers differ between two states: grouped, plus letters with readers.

    Private letters are left out: their viewers are their reader rows in every state.
    """
    a, b = _grouped(world, before), _grouped(world, after)
    changed = sum(n for key, (n, viewers) in a.items() if b[key][1] != viewers)
    ra, rb = _per_letter(world, before, _WITH_READERS), _per_letter(world, after, _WITH_READERS)
    return changed + sum(ra.get(k) != rb.get(k) for k in ra.keys() | rb.keys())


def test_measure_preview_letter_count(perf_world: PerfWorld, perf_report: Report) -> None:
    """As the system: letters whose set of viewers differs between "E" and "own+D"."""
    _enter(perf_world, "system")
    assert _jit() == "off"

    # The first design, once: a row per letter and member (minutes at this size).
    start = time.perf_counter()
    before = _per_letter(perf_world, "E", "d.workspace_id = %s")
    after = _per_letter(perf_world, "own+D", "d.workspace_id = %s")
    per_letter = sum(before.get(k) != after.get(k) for k in before.keys() | after.keys())
    perf_report.preview_per_letter = (time.perf_counter() - start, per_letter)

    changed = -1
    for run in range(RUNS + 1):
        start = time.perf_counter()
        changed = preview_count(perf_world, "E", "own+D")
        if run:  # the first run warms up
            perf_report.preview_runs.append(time.perf_counter() - start)
    perf_report.preview_letters = changed
    directory = _explain_dir()
    if directory is not None:
        # The grouped query of one state, explained: where the preview's time goes.
        _set_state(perf_world, "E")
        sql = viewer_groups_sql("d.workspace_id = %s").replace("%s", f"'{perf_world.workspace_id}'")
        text, _top = _explain(sql, analyze=True)
        (directory / "preview-grouped-E.txt").write_text(f"{sql}\n\n{text}\n")
    if changed != per_letter:
        perf_report.mismatches.append(f"preview: grouped {changed} != per letter {per_letter}")
    assert changed == per_letter


def test_explain_member_filter_and_the_gin_index(
    perf_world: PerfWorld, perf_report: Report
) -> None:
    _set_state(perf_world, "own")
    _enter(perf_world, perf_world.plain_member)
    member = Membership.objects.get(
        workspace_id=perf_world.workspace_id, user_id=perf_world.plain_member
    )
    with CaptureQueriesContext(connection) as captured:
        documents.inbox_counts(member)
    # The member filter's own lookup first, then the count.
    query = captured.captured_queries[-1]
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
    # Recorded, not asserted: the filter is one way to pass P1, not a requirement.


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
