"""The duplicate measurement (spec "How the thresholds are measured", plan Task 3).

Runs only with BRIEVIE_PERF=1, on the throw-away test database: a corpus of 2,000
generated letters (`duplicates_corpus`), 300 positives (the same letter scanned again),
300 hard negatives and 10,000 random pairs. Every positive and hard negative arrives
through the real candidate query (`duplicates._CANDIDATES_SQL`: search index, `pg_trgm`)
and the real decision (`duplicates_facts.decide`), per threshold pair, with and without
the guards. Then the workspace grows to 10,000 and 100,000 letters and `duplicates.detect`
is timed per arrival (warm, median of 7). The report is printed and, when
BRIEVIE_PERF_REPORT names a file, written next to it as `duplicates-measure.md`.

`test_verdict` runs last (file order) and applies the aim: recall >= 0.95, false proposals
<= 2% of hard negatives and 0 of random pairs at the chosen thresholds, < 50 ms per arrival
at 100,000 letters.
"""

import os
import platform
import random
import statistics
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal
from pathlib import Path
from typing import Final
from uuid import UUID

import pytest
from django.db import connection, transaction
from django.utils import timezone
from pytest_django import DjangoDbBlocker

from apps.documents import duplicates, duplicates_facts
from apps.documents.duplicates_facts import LetterFacts, Verdict, decide, number_overlap
from apps.documents.models import Document
from apps.workspaces.context import SYSTEM, workspace_context
from tests.perf import duplicates_corpus as corpus
from tests.perf.duplicates_corpus import Letter, Meta, Strength

pytestmark = pytest.mark.skipif(
    os.environ.get("BRIEVIE_PERF") != "1",
    reason="The duplicate measurement runs with BRIEVIE_PERF=1.",
)

SEED: Final = 20261121
CORPUS: Final = 2_000
POSITIVES: Final = 300
NEGATIVES: Final = 300
RANDOM_PAIRS: Final = 10_000
# A strong reading of the arrival that is wrong (date or amount): the guard's own risk.
MISREAD: Final = 0.02
TEXT_GRID: Final = [Decimal("0.60") + Decimal("0.05") * i for i in range(8)]
NUMBER_GRID: Final = [Decimal("0.40") + Decimal("0.10") * i for i in range(6)]
SIZES: Final = (10_000, 100_000)
TIMED_RUNS: Final = 7
AIM_RECALL: Final = 0.95
AIM_HARD: Final = 0.02
AIM_MS: Final = 50.0
CHUNK: Final = 5_000


@dataclass
class Arrival:
    text: str
    meta: Meta
    power: Strength
    partner: int  # index of the letter already there
    kind: str  # "positive" or a hard negative kind
    found: bool = False
    # Every candidate the query gave: index -> text similarity.
    candidates: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class World:
    workspace_id: UUID
    today: datetime
    letters: list[Letter]
    ids: list[UUID]
    power: list[Strength]
    positives: list[Arrival]
    negatives: list[Arrival]
    size: int = 0


@dataclass
class Results:
    lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    timings: dict[int, list[float]] = field(default_factory=dict)
    explain: str = ""


def _created(today: datetime, meta: Meta) -> datetime:
    """A letter is scanned two days after its date, at noon (never in the future)."""
    when = datetime.combine(meta.letter_date + timedelta(days=2), day_time(12), tzinfo=UTC)
    return min(when, today - timedelta(minutes=1))


def _insert(world: World, letters: list[tuple[UUID, datetime, Letter, Strength]]) -> None:
    """Write letters as the system; a confirmed letter gets its confirmed fields."""
    for start in range(0, len(letters), CHUNK):
        part = letters[start : start + CHUNK]
        with transaction.atomic(), connection.cursor() as c:
            c.execute(
                "SELECT set_config('brievie.workspace_id', %s, true)", [str(world.workspace_id)]
            )
            c.execute("SELECT set_config('brievie.actor', 'system', true)")
            confirmed = [p.confirmed for _, _, _, p in part]
            c.execute(
                "INSERT INTO documents_document (id, created_at, updated_at, workspace_id,"
                " client_id, scanned_by_id, source, status, failure_code, page_count, ocr_text,"
                " sender, document_type, letter_date, summary, summary_by_ai, confirmed_by_id,"
                " confirmed_at, confirmed_by_ai, confirmed_organisation, visibility,"
                " addressee_ids, has_automatic_link)"
                " SELECT id, created, created, %s, gen_random_uuid(), NULL, 'scan',"
                " CASE WHEN confirmed THEN 'done' ELSE 'needs_review' END, '', pages, body,"
                " CASE WHEN confirmed THEN sender ELSE '' END,"
                " CASE WHEN confirmed THEN kind ELSE '' END,"
                " CASE WHEN confirmed THEN dated END, '', false, NULL,"
                " CASE WHEN confirmed THEN created END, false, NULL, 'workspace', '{}', false"
                " FROM unnest(%s::uuid[], %s::timestamptz[], %s::int[], %s::text[], %s::text[],"
                "             %s::text[], %s::date[], %s::boolean[])"
                "   AS t(id, created, pages, body, sender, kind, dated, confirmed)",
                [
                    world.workspace_id,
                    [i for i, _, _, _ in part],
                    [c_ for _, c_, _, _ in part],
                    [lt.meta.pages for _, _, lt, _ in part],
                    [lt.text for _, _, lt, _ in part],
                    [lt.meta.sender for _, _, lt, _ in part],
                    [corpus.TYPES[lt.meta.kind] for _, _, lt, _ in part],
                    [lt.meta.letter_date for _, _, lt, _ in part],
                    confirmed,
                ],
            )
        world.size += len(part)
    with connection.cursor() as c:
        c.execute('VACUUM (ANALYZE) "documents_document"')


@pytest.fixture(scope="module")
def world(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> Iterator[World]:
    rng = random.Random(SEED)  # noqa: S311 - test data, not a secret
    now = timezone.now()
    today = now.date()
    metas = [corpus.new_meta(rng, i % 40, today) for i in range(CORPUS)]
    letters = [Letter(m, corpus.render(m)) for m in metas]
    power = [corpus.strength(rng, already_there=True, misread_rate=0) for _ in letters]
    in_window = [i for i, m in enumerate(metas) if (today - m.letter_date).days <= 170]
    positives = [
        Arrival(
            text=corpus.scan_again(letters[i], rng),
            meta=letters[i].meta,
            power=corpus.strength(rng, already_there=False, misread_rate=MISREAD),
            partner=i,
            kind="positive",
        )
        for i in rng.sample(in_window, POSITIVES)
    ]
    paying = [i for i in in_window if metas[i].kind in corpus.PAYING]
    negatives = []
    for n, i in enumerate(rng.sample(paying, NEGATIVES)):
        how = corpus.NEGATIVE_KINDS[n % len(corpus.NEGATIVE_KINDS)]
        meta = corpus.hard_negative(metas[i], how, rng)
        text = corpus.noisy(corpus.render(meta), rng.uniform(0, 0.01), rng)
        negatives.append(
            Arrival(
                text=text,
                meta=meta,
                power=corpus.strength(rng, already_there=False, misread_rate=0),
                partner=i,
                kind=how,
            )
        )
    workspace_id = uuid.uuid4()
    ids = [uuid.UUID(int=rng.getrandbits(128), version=4) for _ in letters]
    built = World(workspace_id, now, letters, ids, power, positives, negatives)
    with django_db_blocker.unblock():
        with connection.cursor() as c:
            c.execute(
                "INSERT INTO workspaces_workspace (id, created_at, updated_at, name, kind,"
                " modules, allow_member_export, everyone_sees_all, departments_enabled,"
                " admins_read_all, care_is_private, emergency_access)"
                " VALUES (%s, %s, %s, 'Dubbelmeting', 'business', '{}', false, true, false, true,"
                " false, true)",
                [workspace_id, now, now],
            )
        _insert(
            built,
            [(ids[i], _created(now, m.meta), m, power[i]) for i, m in enumerate(letters)],
        )
        yield built


@pytest.fixture(scope="module")
def results() -> Results:
    return Results()


def _candidates(world: World, text: str) -> dict[UUID, Decimal]:
    with connection.cursor() as c:
        c.execute(
            duplicates._CANDIDATES_SQL,  # the real query, measured
            {
                "text": text,
                "terms": duplicates.TERMS,
                "workspace": world.workspace_id,
                "id": uuid.uuid4(),
                "since": world.today - duplicates.WINDOW,
                "limit": duplicates.MAX_CANDIDATES,
            },
        )
        return {row[0]: duplicates._score(float(row[1])) for row in c.fetchall()}


def _likely(
    new: LetterFacts, other: LetterFacts, similarity: Decimal, text: Decimal, numbers: Decimal
) -> bool:
    duplicates_facts.TEXT_SIMILAR_FROM = text  # type: ignore[misc]
    duplicates_facts.NUMBERS_SHARED_FROM = numbers  # type: ignore[misc]
    overlap = number_overlap(new.numbers, other.numbers)
    return decide(new, other, text_similarity=similarity, number_overlap=overlap) == Verdict.LIKELY


def _rate(hits: int, total: int) -> str:
    return f"{hits / total:.3f} ({hits}/{total})" if total else "-"


def test_quality(world: World, results: Results) -> None:
    """Recall and false proposals per threshold pair, with and without the guards."""
    index = {letter_id: i for i, letter_id in enumerate(world.ids)}
    with workspace_context(world.workspace_id, actor=SYSTEM):
        for arrival in [*world.positives, *world.negatives]:
            scored = _candidates(world, arrival.text)
            arrival.candidates = {index[k]: v for k, v in scored.items() if k in index}
            arrival.found = arrival.partner in arrival.candidates
        rng = random.Random(SEED + 7)  # noqa: S311 - test data
        pairs: list[tuple[int, int]] = []
        while len(pairs) < RANDOM_PAIRS:
            a, b = rng.randrange(CORPUS), rng.randrange(CORPUS)
            if a != b:
                pairs.append((a, b))
        random_scores: list[Decimal] = []
        with connection.cursor() as c:
            for start in range(0, len(pairs), 1_000):
                part = pairs[start : start + 1_000]
                c.execute(
                    "SELECT similarity(a.ocr_text, b.ocr_text)"
                    " FROM unnest(%s::uuid[], %s::uuid[]) WITH ORDINALITY AS p(a_id, b_id, n)"
                    " JOIN documents_document a ON a.id = p.a_id"
                    " JOIN documents_document b ON b.id = p.b_id ORDER BY p.n",
                    [[world.ids[a] for a, _ in part], [world.ids[b] for _, b in part]],
                )
                random_scores += [duplicates._score(float(r[0])) for r in c.fetchall()]

    saved = (duplicates_facts.TEXT_SIMILAR_FROM, duplicates_facts.NUMBERS_SHARED_FROM)
    try:
        _evaluate(world, results, pairs, random_scores)
    finally:
        duplicates_facts.TEXT_SIMILAR_FROM, duplicates_facts.NUMBERS_SHARED_FROM = saved  # type: ignore[misc]


def _evaluate(
    world: World, results: Results, pairs: list[tuple[int, int]], random_scores: list[Decimal]
) -> None:
    base = {
        guards: [
            corpus.facts(lt.text, lt.meta, world.power[i], guards=guards)
            for i, lt in enumerate(world.letters)
        ]
        for guards in (True, False)
    }
    arrivals = {
        guards: [
            corpus.facts(a.text, a.meta, a.power, guards=guards)
            for a in [*world.positives, *world.negatives]
        ]
        for guards in (True, False)
    }
    n_pos = len(world.positives)
    lines = results.lines
    retrieved = sum(a.found for a in world.positives)
    lines += [
        "## Quality (2,000 letters, 300 positives, 300 hard negatives, 10,000 random pairs)",
        "",
        f"Positives whose letter the candidate query finds: {_rate(retrieved, n_pos)}. "
        f"Hard negatives whose look-alike it finds: "
        f"{_rate(sum(a.found for a in world.negatives), len(world.negatives))}.",
        "",
        "| text | numbers | recall | recall no guards | hard neg. | hard neg. no guards |"
        " random | random no guards | other proposals per arrival |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    chosen: dict[str, float] = {}
    for text in TEXT_GRID:
        for numbers in NUMBER_GRID:
            row = {}
            for guards in (True, False):
                hits = sum(
                    a.found
                    and _likely(
                        arrivals[guards][k],
                        base[guards][a.partner],
                        a.candidates[a.partner],
                        text,
                        numbers,
                    )
                    for k, a in enumerate(world.positives)
                )
                false_hard = sum(
                    a.found
                    and _likely(
                        arrivals[guards][n_pos + k],
                        base[guards][a.partner],
                        a.candidates[a.partner],
                        text,
                        numbers,
                    )
                    for k, a in enumerate(world.negatives)
                )
                false_random = sum(
                    _likely(base[guards][a], base[guards][b], s, text, numbers)
                    for (a, b), s in zip(pairs, random_scores, strict=True)
                )
                row[guards] = (hits, false_hard, false_random)
            other = sum(
                _likely(arrivals[True][k], base[True][i], s, text, numbers)
                for k, a in enumerate([*world.positives, *world.negatives])
                for i, s in a.candidates.items()
                if i != a.partner
            )
            lines.append(
                f"| {text} | {numbers} | {_rate(row[True][0], n_pos)} | "
                f"{_rate(row[False][0], n_pos)} | {_rate(row[True][1], NEGATIVES)} | "
                f"{_rate(row[False][1], NEGATIVES)} | {row[True][2]} | {row[False][2]} | "
                f"{other / (n_pos + NEGATIVES):.3f} |"
            )
            if (text, numbers) == (Decimal("0.80"), Decimal("0.70")):
                chosen = {
                    "recall": row[True][0] / n_pos,
                    "hard": row[True][1] / NEGATIVES,
                    "random": row[True][2],
                }
    lines += ["", "Hard negatives proposed at 0.80 / 0.70 with guards, per kind:", ""]
    for how in corpus.NEGATIVE_KINDS:
        group = [(n_pos + k, a) for k, a in enumerate(world.negatives) if a.kind == how]
        proposed = sum(
            a.found
            and _likely(
                arrivals[True][k],
                base[True][a.partner],
                a.candidates[a.partner],
                Decimal("0.80"),
                Decimal("0.70"),
            )
            for k, a in group
        )
        lines.append(f"- {how}: {_rate(proposed, len(group))}")
    sims = sorted(a.candidates[a.partner] for a in world.positives if a.found)
    if sims:
        lines += [
            "",
            f"Text similarity of found positives: min {sims[0]}, 5th percentile "
            f"{sims[len(sims) // 20]}, median {statistics.median(sims)}.",
        ]
    lines.append(f"Highest text similarity among random pairs: {max(random_scores)}.")
    if chosen["recall"] < AIM_RECALL:
        results.failures.append(f"recall {chosen['recall']:.3f} < {AIM_RECALL}")
    if chosen["hard"] > AIM_HARD:
        results.failures.append(f"hard negatives {chosen['hard']:.3f} > {AIM_HARD}")
    if chosen["random"] > 0:
        results.failures.append(f"{chosen['random']} random pairs proposed")


def _grow(world: World, size: int) -> None:
    rng = random.Random(SEED + size)  # noqa: S311 - test data
    today = world.today.date()
    extra = []
    for _ in range(size - world.size):
        meta = corpus.new_meta(rng, rng.randrange(40), today)
        extra.append(
            (
                uuid.UUID(int=rng.getrandbits(128), version=4),
                _created(world.today, meta),
                Letter(meta, corpus.render(meta)),
                corpus.strength(rng, already_there=True, misread_rate=0),
            )
        )
    _insert(world, extra)


def _time_detect(world: World, results: Results, size: int) -> None:
    timings = []
    with workspace_context(world.workspace_id, actor=SYSTEM):
        for arrival in world.positives[:TIMED_RUNS]:
            document = Document.objects.create(
                workspace_id=world.workspace_id,
                client_id=uuid.uuid4(),
                page_count=arrival.text.count("\f") + 1,
                ocr_text=arrival.text,
                status=Document.Status.PROCESSING,
            )
            for timed in (False, True):
                with transaction.atomic():
                    start = time.perf_counter()
                    duplicates.detect(document)
                    took = time.perf_counter() - start
                    transaction.set_rollback(True)
                if timed:
                    timings.append(took * 1000)
        if size == SIZES[-1]:
            with connection.cursor() as c:
                c.execute(
                    "EXPLAIN (ANALYZE, BUFFERS) " + duplicates._CANDIDATES_SQL,
                    {
                        "text": world.positives[0].text,
                        "terms": duplicates.TERMS,
                        "workspace": world.workspace_id,
                        "id": uuid.uuid4(),
                        "since": world.today - duplicates.WINDOW,
                        "limit": duplicates.MAX_CANDIDATES,
                    },
                )
                results.explain = "\n".join(str(r[0]) for r in c.fetchall())
    results.timings[size] = timings


@pytest.fixture(scope="module")
def grown_10000(world: World, django_db_blocker: DjangoDbBlocker) -> World:
    with django_db_blocker.unblock():
        _grow(world, SIZES[0])
    return world


@pytest.fixture(scope="module")
def grown_100000(grown_10000: World, django_db_blocker: DjangoDbBlocker) -> World:
    with django_db_blocker.unblock():
        _grow(grown_10000, SIZES[1])
    return grown_10000


def test_detect_at_10000(grown_10000: World, results: Results) -> None:
    _time_detect(grown_10000, results, SIZES[0])


def test_detect_at_100000(grown_100000: World, results: Results) -> None:
    _time_detect(grown_100000, results, SIZES[1])


def _server() -> list[str]:
    lines = [f"- machine: {platform.system()} {platform.machine()}, {os.cpu_count()} CPUs"]
    with connection.cursor() as c:
        c.execute("SELECT version()")
        row = c.fetchone()
        lines.append(f"- postgres: {row[0] if row else '?'}")
        for setting in ("shared_buffers", "work_mem", "jit"):
            c.execute(f"SHOW {setting}")
            row = c.fetchone()
            lines.append(f"- {setting}: {row[0] if row else '?'}")
    return lines


def test_verdict(results: Results) -> None:
    """Runs last (file order): writes the report and applies the aim."""
    lines = ["# Duplicate letters: measurement", "", *_server(), "", *results.lines, ""]
    lines += ["## Time of `detect` per arrival (warm, median of 7)", ""]
    for size, runs in sorted(results.timings.items()):
        median = statistics.median(runs)
        lines.append(f"- {size:,} letters: median {median:.1f} ms, slowest {max(runs):.1f} ms")
        if size == SIZES[-1] and median >= AIM_MS:
            results.failures.append(f"detect {median:.1f} ms >= {AIM_MS} ms at {size:,}")
    lines += ["", "## Candidate query at 100,000 letters", "", "```", results.explain, "```"]
    if results.failures:
        lines += ["", "## Aim missed", "", *(f"- {f}" for f in results.failures)]
    text = "\n".join(lines)
    value = os.environ.get("BRIEVIE_PERF_REPORT", "")
    if value:
        Path(value).with_name("duplicates-measure.md").write_text(text)
    print(text)  # also in the job log
    assert not results.failures, "\n".join(results.failures)
