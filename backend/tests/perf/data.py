"""The 100,000-letter world of S 4.9.5, for the visibility measurement.

Two halves. `make_plan` decides everything (who is in which department, which letter is
private, on whom, scanned by whom, which labels, tasks and reader rows) in plain Python
from a fixed seed, so it is tested without a database (tests/perf/test_data_plan.py)
and every run measures the same world. `build` writes that plan with a handful of
`INSERT … SELECT … FROM unnest(…)` statements as the system, every `NOT NULL` column
explicit, then `VACUUM ANALYZE`, as autovacuum would have done on a real server.

Shape (S 4.9.5, plan Task 1): 100,000 letters, 10 % private; 25 members, each linked to
a person, and 25 persons without an account; 5 departments of 5 members; 2 admins; 1
flagged sorter; addressees 60 % one person, 25 % one department, 10 % a person and a
department, 5 % none; 20 labels with 100,000 links; 20,000 tasks; 300,000 events; a
private letter has one `addressee` and one `share` reader row.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, NamedTuple
from uuid import UUID

from django.db import connection, transaction

SEED: Final = 20261120

# Members by index. The first five are in no department: the two admins, the flagged
# sorter, the plain member and one more; the other twenty fill the departments.
ADMINS: Final = (0, 1)
SORTER: Final = 2
PLAIN: Final = 3
OUTSIDE_DEPARTMENTS: Final = 5


@dataclass(frozen=True)
class Shape:
    letters: int = 100_000
    private_percent: int = 10
    members: int = 25  # each linked to one person
    unlinked_persons: int = 25
    departments: int = 5
    department_size: int = 5
    labels: int = 20
    label_links: int = 100_000
    tasks: int = 20_000
    letterless_tasks: int = 200  # of `tasks`, made by a member (the R39 arm)
    events_per_letter: int = 3
    # Percentages of letters per addressee pattern; they add up to 100.
    addressee_mix: tuple[tuple[str, int], ...] = (
        ("person", 60),
        ("department", 25),
        ("both", 10),
        ("none", 5),
    )
    status_mix: tuple[tuple[str, int], ...] = (
        ("done", 85),
        ("needs_review", 8),
        ("processing", 5),
        ("failed", 2),
    )
    sorter_scans_percent: int = 70
    # An autopilot workspace: links the AI made, letters it confirmed.
    automatic_percent: int = 10  # of letters (their addressee links) and of label links
    by_ai_percent: int = 5  # of done letters
    public_share_percent: int = 1  # non-private letters shared with one more member
    ocr_words: int = 45  # 45 hex words of 33 characters: about 1,500 characters a letter
    years: int = 5


DEFAULT: Final = Shape()


class Letter(NamedTuple):
    private: bool
    status: str
    scanned_by: int  # member index
    persons: tuple[int, ...]  # person indexes (members' persons first, then unlinked)
    departments: tuple[int, ...]  # department indexes
    age_seconds: int


class TaskRow(NamedTuple):
    letter: int | None
    open: bool
    assigned_to: int | None
    created_by: int | None
    deadline_days: int | None


class Reader(NamedTuple):
    letter: int
    member: int
    reason: str


@dataclass(frozen=True)
class Plan:
    shape: Shape
    roles: list[str]  # per member: "admin" or "member"
    departments: list[list[int]]  # per department: member indexes
    letters: list[Letter]
    label_links: list[tuple[int, int]]  # (letter, label)
    tasks: list[TaskRow]
    readers: list[Reader]
    automatic_addressees: frozenset[int]  # letters whose addressee links are automatic
    automatic_labels: frozenset[int]  # indexes into label_links
    by_ai: frozenset[int]  # letters the AI confirmed

    @property
    def department_member(self) -> int:
        """A member in exactly one department, and in no other role."""
        counts = [sum(m in d for d in self.departments) for m in range(self.shape.members)]
        return next(m for m in range(OUTSIDE_DEPARTMENTS, self.shape.members) if counts[m] == 1)


def _exact_mix(rng: random.Random, n: int, mix: Sequence[tuple[str, int]]) -> list[str]:
    """Exactly the given percentages of `n` (the last one takes the rounding), shuffled."""
    if sum(percent for _, percent in mix) != 100:
        raise ValueError("A mix adds up to 100.")
    values: list[str] = []
    for name, percent in mix[:-1]:
        values += [name] * (n * percent // 100)
    values += [mix[-1][0]] * (n - len(values))
    rng.shuffle(values)
    return values


def _departments(shape: Shape) -> list[list[int]]:
    """`departments` groups over the members after the first five, overlapping by one.

    Each department has its own block of members plus the first of the next block, so
    some members are in two departments, as in a real office.
    """
    pool = list(range(OUTSIDE_DEPARTMENTS, shape.members))
    block = len(pool) // shape.departments
    if block + 1 != shape.department_size:
        raise ValueError("The members after the first five do not fill the departments.")
    return [
        [*pool[d * block : (d + 1) * block], pool[((d + 1) % shape.departments) * block]]
        for d in range(shape.departments)
    ]


def make_plan(shape: Shape = DEFAULT, seed: int = SEED) -> Plan:
    rng = random.Random(seed)  # noqa: S311 - test data, not a secret
    persons = shape.members + shape.unlinked_persons
    departments = _departments(shape)
    patterns = _exact_mix(rng, shape.letters, shape.addressee_mix)
    statuses = _exact_mix(rng, shape.letters, shape.status_mix)
    private = [True] * (shape.letters * shape.private_percent // 100)
    private += [False] * (shape.letters - len(private))
    rng.shuffle(private)
    span = shape.years * 365 * 24 * 3600

    letters = []
    for i in range(shape.letters):
        pattern = patterns[i]
        on_persons = (rng.randrange(persons),) if pattern in ("person", "both") else ()
        on_departments = (
            (rng.randrange(shape.departments),) if pattern in ("department", "both") else ()
        )
        scanner = (
            SORTER
            if rng.randrange(100) < shape.sorter_scans_percent
            else rng.randrange(shape.members)
        )
        letters.append(
            Letter(
                private=private[i],
                status=statuses[i],
                scanned_by=scanner,
                persons=on_persons,
                departments=on_departments,
                age_seconds=rng.randrange(span),
            )
        )

    pairs = rng.sample(range(shape.letters * shape.labels), shape.label_links)
    label_links = sorted((p // shape.labels, p % shape.labels) for p in pairs)

    on_letters = shape.tasks - shape.letterless_tasks
    tasks = [
        TaskRow(
            letter=letter,
            open=rng.randrange(100) < 70,
            assigned_to=rng.randrange(shape.members) if rng.randrange(2) else None,
            created_by=None,
            deadline_days=rng.randrange(-60, 120) if rng.randrange(10) else None,
        )
        for letter in rng.sample(range(shape.letters), on_letters)
    ]
    tasks += [
        TaskRow(
            letter=None,
            open=True,
            assigned_to=rng.randrange(shape.members),
            created_by=rng.randrange(shape.members),
            deadline_days=rng.randrange(0, 60),
        )
        for _ in range(shape.letterless_tasks)
    ]

    readers = []
    for i, letter in enumerate(letters):
        if not letter.private:
            continue
        if letter.persons and letter.persons[0] < shape.members:
            first = letter.persons[0]  # the member linked to the person
        elif letter.departments:
            first = departments[letter.departments[0]][0]
        else:
            first = letter.scanned_by
        others = [m for m in range(shape.members) if m != first]
        readers.append(Reader(i, first, "addressee"))
        readers.append(Reader(i, rng.choice(others), "share"))

    # Drawn from their own generator, so the rest of the world stays as it was.
    extra = random.Random(seed + 2)  # noqa: S311 - test data, not a secret
    automatic_addressees = frozenset(
        i for i in range(shape.letters) if extra.randrange(100) < shape.automatic_percent
    )
    automatic_labels = frozenset(
        j for j in range(len(label_links)) if extra.randrange(100) < shape.automatic_percent
    )
    by_ai = frozenset(
        i
        for i, x in enumerate(letters)
        if x.status == "done" and extra.randrange(100) < shape.by_ai_percent
    )
    for i, letter in enumerate(letters):
        if not letter.private and extra.randrange(100) < shape.public_share_percent:
            readers.append(Reader(i, extra.randrange(shape.members), "share"))

    return Plan(
        shape=shape,
        roles=["admin" if m in ADMINS else "member" for m in range(shape.members)],
        departments=departments,
        letters=letters,
        label_links=label_links,
        tasks=tasks,
        readers=readers,
        automatic_addressees=automatic_addressees,
        automatic_labels=automatic_labels,
        by_ai=by_ai,
    )


@dataclass(frozen=True)
class PerfWorld:
    workspace_id: UUID
    plain_member: UUID
    department_member: UUID
    sorter: UUID
    admin: UUID
    plain_person_id: UUID
    own_person: dict[UUID, UUID]  # member -> the person linked to them


def _ids(rng: random.Random, n: int) -> list[UUID]:
    return [UUID(int=rng.getrandbits(128), version=4) for _ in range(n)]


_SENDERS: Final = [f"Afzender {n:03d}" for n in range(200)]
_TYPES: Final = ["invoice", "assessment", "bill", "reminder", "policy", "information", "other"]
_TASK_TYPES: Final = ["pay", "respond", "object", "renew", "other"]


def build(workspace_id: UUID, shape: Shape = DEFAULT, seed: int = SEED) -> PerfWorld:
    """Write the plan into the workspace `workspace_id` (created here), as the system.

    Needs the migrated visibility schema (Task 7) and a connection outside
    any transaction: the data is committed, then vacuumed and analysed.
    """
    plan = make_plan(shape, seed)
    rng = random.Random(seed + 1)  # noqa: S311 - test data, not a secret
    users = _ids(rng, shape.members)
    persons = _ids(rng, shape.members + shape.unlinked_persons)
    departments = _ids(rng, shape.departments)
    labels = _ids(rng, shape.labels)
    letters = _ids(rng, shape.letters)
    now = datetime.now(UTC)
    today = now.date()

    def addressee_ids(plan: Plan, i: int) -> list[UUID]:
        letter = plan.letters[i]
        return [persons[p] for p in letter.persons] + [departments[d] for d in letter.departments]

    with transaction.atomic(), connection.cursor() as c:
        c.execute("SELECT set_config('brievie.workspace_id', %s, true)", [str(workspace_id)])
        c.execute("SELECT set_config('brievie.actor', 'system', true)")
        c.execute(
            "INSERT INTO accounts_user (id, password, last_login, is_superuser, created_at,"
            " updated_at, email, name, language, explanation_language, is_active,"
            " is_server_admin)"
            " SELECT u, '!', NULL, false, %s, %s, 'perf-' || n || '@example.test',"
            " 'Lid ' || n, 'nl', 'nl', true, false"
            " FROM unnest(%s::uuid[]) WITH ORDINALITY AS t(u, n)",
            [now, now, users],
        )
        c.execute(
            "INSERT INTO workspaces_workspace (id, created_at, updated_at, name, kind, modules,"
            " allow_member_export, everyone_sees_all, departments_enabled, admins_read_all,"
            " care_is_private, emergency_access)"
            " VALUES (%s, %s, %s, 'Meetkantoor', 'business', '{}', false, true, false, true,"
            " false, true)",
            [workspace_id, now, now],
        )
        c.execute(
            "INSERT INTO workspaces_membership (id, created_at, updated_at, user_id,"
            " workspace_id, role, sorts_post)"
            " SELECT gen_random_uuid(), %s, %s, u, %s, r, n - 1 = %s"
            " FROM unnest(%s::uuid[], %s::text[]) WITH ORDINALITY AS t(u, r, n)",
            [now, now, workspace_id, SORTER, users, plan.roles],
        )
        person_users: list[UUID | None] = [*users, *([None] * shape.unlinked_persons)]
        c.execute(
            "INSERT INTO people_person (id, created_at, updated_at, workspace_id, display_name,"
            " name_variants, user_id, archived_at, kind)"
            " SELECT p, %s, %s, %s, 'Persoon ' || n, '[]', u, NULL, 'person'"
            " FROM unnest(%s::uuid[], %s::uuid[]) WITH ORDINALITY AS t(p, u, n)",
            [now, now, workspace_id, persons, person_users],
        )
        c.execute(
            "INSERT INTO people_person (id, created_at, updated_at, workspace_id, display_name,"
            " name_variants, user_id, archived_at, kind)"
            " SELECT p, %s, %s, %s, 'Afdeling ' || n, '[]', NULL, NULL, 'department'"
            " FROM unnest(%s::uuid[]) WITH ORDINALITY AS t(p, n)",
            [now, now, workspace_id, departments],
        )
        memberships = [(d, m) for d, members in enumerate(plan.departments) for m in members]
        c.execute(
            "INSERT INTO people_departmentmember (id, created_at, updated_at, workspace_id,"
            " department_id, user_id, is_lead)"
            " SELECT gen_random_uuid(), %s, %s, %s, d, u, l"
            " FROM unnest(%s::uuid[], %s::uuid[], %s::boolean[]) AS t(d, u, l)",
            [
                now,
                now,
                workspace_id,
                [departments[d] for d, _ in memberships],
                [users[m] for _, m in memberships],
                [plan.departments[d][0] == m for d, m in memberships],
            ],
        )
        c.execute(
            "INSERT INTO labels_label (id, created_at, updated_at, workspace_id, name, color,"
            " system_key)"
            " SELECT l, %s, %s, %s, 'Label ' || n, 'grey', ''"
            " FROM unnest(%s::uuid[]) WITH ORDINALITY AS t(l, n)",
            [now, now, workspace_id, labels],
        )
        c.execute(
            "INSERT INTO documents_document (id, created_at, updated_at, workspace_id, client_id,"
            " scanned_by_id, source, status, failure_code, page_count, ocr_text, sender,"
            " document_type, letter_date, summary, summary_by_ai, confirmed_by_id, confirmed_at,"
            " confirmed_by_ai, confirmed_organisation, visibility, addressee_ids)"
            " SELECT id, %s - make_interval(secs => age), %s - make_interval(secs => age), %s,"
            " gen_random_uuid(), scanner, 'scan', status,"
            " CASE WHEN status = 'failed' THEN 'ocr_failed' ELSE '' END, (1 + n %% 3)::int,"
            " (SELECT string_agg(md5(n::text || '-' || w::text), ' ')"
            "    FROM generate_series(1, %s) w),"
            " sender, kind, (%s::date - (age / 86400 + 3)::int),"
            " 'Samenvatting van brief ' || n, false,"
            " CASE WHEN status = 'done' THEN scanner END,"
            " CASE WHEN status = 'done' THEN %s - make_interval(secs => age) END,"
            " by_ai, NULL, CASE WHEN is_private THEN 'restricted' ELSE 'workspace' END,"
            " addressees::uuid[]"
            " FROM unnest(%s::uuid[], %s::uuid[], %s::text[], %s::int[], %s::boolean[],"
            "             %s::text[], %s::text[], %s::boolean[], %s::text[])"
            "   WITH ORDINALITY AS t(id, scanner, status, age, is_private, sender, kind, by_ai,"
            "                        addressees, n)",
            [
                now,
                now,
                workspace_id,
                shape.ocr_words,
                today,
                now,
                letters,
                [users[x.scanned_by] for x in plan.letters],
                [x.status for x in plan.letters],
                [x.age_seconds for x in plan.letters],
                [x.private for x in plan.letters],
                [_SENDERS[i % len(_SENDERS)] for i in range(shape.letters)],
                [_TYPES[i % len(_TYPES)] for i in range(shape.letters)],
                [i in plan.by_ai for i in range(shape.letters)],
                # Written with the letter, sorted as the sync will (ORDER BY id), so the
                # table is not rewritten by an UPDATE of every row: a bulk rewrite leaves
                # it twice its size, which a server that grew letter by letter is not.
                [
                    "{" + ",".join(str(a) for a in sorted(addressee_ids(plan, i))) + "}"
                    for i in range(shape.letters)
                ],
            ],
        )
        links = [(i, persons[p]) for i, x in enumerate(plan.letters) for p in x.persons]
        links += [(i, departments[d]) for i, x in enumerate(plan.letters) for d in x.departments]
        c.execute(
            "INSERT INTO documents_documentaddressee (id, created_at, updated_at, workspace_id,"
            " document_id, person_id, automatic)"
            " SELECT gen_random_uuid(), %s, %s, %s, d, p, a"
            " FROM unnest(%s::uuid[], %s::uuid[], %s::boolean[]) AS t(d, p, a)",
            [
                now,
                now,
                workspace_id,
                [letters[i] for i, _ in links],
                [p for _, p in links],
                [i in plan.automatic_addressees for i, _ in links],
            ],
        )
        c.execute(
            "INSERT INTO documents_documentlabel (id, created_at, updated_at, workspace_id,"
            " document_id, label_id, automatic)"
            " SELECT gen_random_uuid(), %s, %s, %s, d, l, a"
            " FROM unnest(%s::uuid[], %s::uuid[], %s::boolean[]) AS t(d, l, a)",
            [
                now,
                now,
                workspace_id,
                [letters[i] for i, _ in plan.label_links],
                [labels[label] for _, label in plan.label_links],
                [j in plan.automatic_labels for j in range(len(plan.label_links))],
            ],
        )
        c.execute(
            "INSERT INTO tasks_task (id, created_at, updated_at, workspace_id, document_id, type,"
            " description, deadline, deadline_source, deadline_quote, status, assigned_to_id,"
            " completed_at, created_by_ai, created_by_id)"
            " SELECT gen_random_uuid(), %s, %s, %s, d, (%s::text[])[1 + (n %% %s)::int],"
            " 'Taak ' || n, %s::date + days, 'in_document', '',"
            " CASE WHEN is_open THEN 'open' ELSE 'done' END, assignee,"
            " CASE WHEN is_open THEN NULL ELSE %s END, false, maker"
            " FROM unnest(%s::uuid[], %s::boolean[], %s::uuid[], %s::uuid[], %s::int[])"
            "   WITH ORDINALITY AS t(d, is_open, assignee, maker, days, n)",
            [
                now,
                now,
                workspace_id,
                _TASK_TYPES,
                len(_TASK_TYPES),
                today,
                now,
                [None if t.letter is None else letters[t.letter] for t in plan.tasks],
                [t.open for t in plan.tasks],
                [None if t.assigned_to is None else users[t.assigned_to] for t in plan.tasks],
                [None if t.created_by is None else users[t.created_by] for t in plan.tasks],
                [t.deadline_days for t in plan.tasks],
            ],
        )
        c.execute(
            "INSERT INTO documents_documentevent (id, created_at, updated_at, workspace_id,"
            " document_id, kind, origin, actor_id, person_id, person_name, label_id, label_name,"
            " rule_id, rule_name, undoes_id, note)"
            " SELECT gen_random_uuid(), d.created_at, d.created_at, d.workspace_id, d.id,"
            " (ARRAY['addressee_added', 'label_added', 'confirmed'])[1 + ((g - 1) %% 3)::int],"
            " 'user', d.scanned_by_id, NULL, '', NULL, '', NULL, '', NULL, ''"
            " FROM documents_document d CROSS JOIN generate_series(1, %s) g"
            " WHERE d.workspace_id = %s",
            [shape.events_per_letter, workspace_id],
        )
        c.execute(
            "INSERT INTO documents_documentreader (id, created_at, updated_at, workspace_id,"
            " document_id, user_id, reason, expires_at)"
            " SELECT gen_random_uuid(), %s, %s, %s, d, u, r, NULL"
            " FROM unnest(%s::uuid[], %s::uuid[], %s::text[]) AS t(d, u, r)",
            [
                now,
                now,
                workspace_id,
                [letters[r.letter] for r in plan.readers],
                [users[r.member] for r in plan.readers],
                [r.reason for r in plan.readers],
            ],
        )

    with connection.cursor() as c:
        for table in (
            "accounts_user",
            "workspaces_workspace",
            "workspaces_membership",
            "people_person",
            "people_departmentmember",
            "labels_label",
            "documents_document",
            "documents_documentsearch",
            "documents_documentaddressee",
            "documents_documentlabel",
            "documents_documentevent",
            "documents_documentreader",
            "tasks_task",
        ):
            c.execute(f'VACUUM (ANALYZE) "{table}"')

    department_member = plan.department_member
    return PerfWorld(
        workspace_id=workspace_id,
        plain_member=users[PLAIN],
        department_member=users[department_member],
        sorter=users[SORTER],
        admin=users[ADMINS[0]],
        plain_person_id=persons[PLAIN],
        own_person={users[m]: persons[m] for m in range(shape.members)},
    )
