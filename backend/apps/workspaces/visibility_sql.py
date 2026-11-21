"""The one source of "who may see this letter" in SQL (spec S 4.9).

Strings only, no Django import: the migrations (Task 7, through `rls`) and
`documents.visibility.viewers()` (Task 8) render the rule from here, so there is no
second copy of it.

The four functions read the switches, roles, sorting flags, person links and
department memberships live from their tables, for a user given as a parameter. The
policy passes the actor; `viewer_pairs_sql` passes every member of the workspace, as the
system, to answer the same question about everyone else.
"""

from typing import Final

ACTOR: Final = "(SELECT public.brievie_actor_id())"

_WORKSPACE: Final = "NULLIF(pg_catalog.current_setting('brievie.workspace_id', true), '')::uuid"
_ATTRIBUTES: Final = "LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER SET search_path = ''"

# Arm 1: everyone sees all, or the user is an admin and admins read all.
_SEES_ALL: Final = f"""CREATE FUNCTION public.brievie_sees_all_post(uid uuid) RETURNS boolean
{_ATTRIBUTES}
AS $$
  SELECT COALESCE((
    SELECT w.everyone_sees_all OR (w.admins_read_all AND m.role = 'admin')
    FROM public.workspaces_workspace w
    JOIN public.workspaces_membership m ON m.workspace_id = w.id
    WHERE w.id = {_WORKSPACE}
      AND m.user_id = uid), false)
$$"""  # noqa: S608 - fixed SQL, no input

# Arms 2 and 3: the persons linked to the user, and the user's departments while on.
# As created by workspaces/0007 (before "Beheerd door"); kept for that migration only.
_MY_POST_IDS_0007: Final = f"""CREATE FUNCTION public.brievie_my_post_ids(uid uuid) RETURNS uuid[]
{_ATTRIBUTES}
AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{{}}') FROM (
    SELECT p.id FROM public.people_person p
     WHERE p.user_id = uid AND p.kind = 'person'
    UNION ALL
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE dm.user_id = uid AND w.departments_enabled) s
$$"""  # noqa: S608 - fixed SQL, no input

# Arms 2 and 3: the persons linked to the user, the persons without an account the user
# manages ("Beheerd door", Mensen slice C), and the user's departments while on.
_MY_POST_IDS: Final = f"""CREATE FUNCTION public.brievie_my_post_ids(uid uuid) RETURNS uuid[]
{_ATTRIBUTES}
AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{{}}') FROM (
    SELECT p.id FROM public.people_person p
     WHERE p.user_id = uid AND p.kind = 'person'
    UNION ALL
    SELECT p.id FROM public.people_person p
     WHERE p.managed_by_id = uid AND p.user_id IS NULL AND p.kind = 'person'
    UNION ALL
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE dm.user_id = uid AND w.departments_enabled) s
$$"""  # noqa: S608 - fixed SQL, no input

# Arm 4: addressees somebody reads through: persons with an account, and departments
# with members while departments are on. As created by workspaces/0007.
_COVERED_POST_IDS_0007: Final = f"""CREATE FUNCTION public.brievie_covered_post_ids() RETURNS uuid[]
{_ATTRIBUTES}
AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{{}}') FROM (
    SELECT p.id FROM public.people_person p WHERE p.user_id IS NOT NULL
    UNION
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE w.departments_enabled) s
$$"""  # noqa: S608 - fixed SQL, no input

# Arm 4: addressees somebody reads through: persons with an account, persons without one
# that a member manages, and departments with members while departments are on.
_COVERED_POST_IDS: Final = f"""CREATE FUNCTION public.brievie_covered_post_ids() RETURNS uuid[]
{_ATTRIBUTES}
AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{{}}') FROM (
    SELECT p.id FROM public.people_person p WHERE p.user_id IS NOT NULL
    UNION
    SELECT p.id FROM public.people_person p
     WHERE p.managed_by_id IS NOT NULL AND p.user_id IS NULL
    UNION
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE w.departments_enabled) s
$$"""  # noqa: S608 - fixed SQL, no input

# Arm 4: the user sorts post: flagged, or an admin while nobody is flagged.
_SORTS_POST: Final = f"""CREATE FUNCTION public.brievie_actor_sorts_post(uid uuid) RETURNS boolean
{_ATTRIBUTES}
AS $$
  SELECT COALESCE((
    SELECT m.sorts_post
        OR (m.role = 'admin' AND NOT EXISTS (
              SELECT 1 FROM public.workspaces_membership o
               WHERE o.workspace_id = m.workspace_id AND o.sorts_post))
    FROM public.workspaces_membership m
    WHERE m.workspace_id = {_WORKSPACE}
      AND m.user_id = uid), false)
$$"""  # noqa: S608 - fixed SQL, no input

DROP_VISIBILITY_FUNCTIONS: Final[list[str]] = [
    "DROP FUNCTION public.brievie_sees_all_post(uuid)",
    "DROP FUNCTION public.brievie_my_post_ids(uuid)",
    "DROP FUNCTION public.brievie_covered_post_ids()",
    "DROP FUNCTION public.brievie_actor_sorts_post(uuid)",
]


def visibility_functions_sql() -> list[str]:
    """The four `CREATE FUNCTION` statements of S 4.9.1, the user as parameter."""
    return [_SEES_ALL, _MY_POST_IDS, _COVERED_POST_IDS, _SORTS_POST]


def visibility_functions_sql_0007() -> list[str]:
    """The four functions as workspaces/0007 created them (frozen: a migration replays the
    text of its own time; the managed-post arms read a column made later)."""
    return [_SEES_ALL, _MY_POST_IDS_0007, _COVERED_POST_IDS_0007, _SORTS_POST]


def _or_replace(statement: str) -> str:
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def managed_post_functions_sql() -> list[str]:
    """workspaces/0009: the two helpers with the managed-post arms ("Beheerd door")."""
    return [_or_replace(_MY_POST_IDS), _or_replace(_COVERED_POST_IDS)]


def managed_post_functions_reverse_sql() -> list[str]:
    """Back to the text of workspaces/0007 (the policies call the helpers: never dropped)."""
    return [_or_replace(_MY_POST_IDS_0007), _or_replace(_COVERED_POST_IDS_0007)]


def document_condition(
    *,
    user: str,
    sees_all: str,
    my_ids: str,
    sorts: str,
    covered: str,
    table: str = '"documents_document"',
) -> str:
    """The S 4.9.3 condition without its leading system arm, every column qualified.

    Every argument is a fixed SQL fragment chosen by the caller in code, never user
    input: the policy passes the helper calls, `viewer_pairs_sql` the member columns.

    The reader-row arm comes last, unlike in the spec's text: PostgreSQL evaluates the
    arms of an OR in the order written, and this is the only arm that probes another
    table for every row. Last, it runs only for letters the cheap arms leave out (the
    private ones, and the ones the user does not see otherwise). Same truth table.
    """
    return (
        f"(({table}.visibility = 'workspace' AND ("  # noqa: S608 - fixed fragments, no input
        f"{sees_all}"
        f" OR {table}.addressee_ids OPERATOR(pg_catalog.&&) {my_ids}"
        f" OR ({table}.scanned_by_id = {user} AND {table}.status <> 'done')"
        f" OR ({sorts} AND NOT {table}.addressee_ids OPERATOR(pg_catalog.&&) {covered})))"
        " OR EXISTS ("
        'SELECT 1 FROM "documents_documentreader" r'
        f" WHERE r.document_id = {table}.id"
        f" AND r.user_id = {user}"
        " AND (r.expires_at IS NULL OR r.expires_at > pg_catalog.now())))"
    )


POLICY_CONDITION: Final[str] = (
    "(SELECT public.brievie_is_system()) OR ("
    + document_condition(
        user=ACTOR,
        sees_all=f"(SELECT public.brievie_sees_all_post({ACTOR}))",
        my_ids=f"(SELECT public.brievie_my_post_ids({ACTOR}))",
        sorts=f"(SELECT public.brievie_actor_sorts_post({ACTOR}))",
        covered="(SELECT public.brievie_covered_post_ids())",
    )
    + ")"
)


def viewer_pairs_sql(document_filter: str) -> str:
    """(document id, user id) for every member of the current workspace who sees it.

    `document_filter` is a fixed SQL fragment over `d` with `%s` placeholders for its
    values (never user text). Run as the system: the document policy then lets every
    row through and this condition alone decides, with each member's id as the user.

    `m` and `c` are MATERIALIZED: referenced once, PostgreSQL would otherwise inline
    them and call the helper functions (not inlinable: they `SET search_path`) once per
    letter and member instead of once per member.
    """
    if ";" in document_filter or "--" in document_filter or "/*" in document_filter:
        raise ValueError("document_filter must be one SQL fragment without comments.")
    condition = document_condition(
        user="m.user_id",
        sees_all="m.sees_all",
        my_ids="m.my_ids",
        sorts="m.sorts",
        covered="c.covered",
        table="d",
    )
    return (
        "WITH m AS MATERIALIZED ("  # noqa: S608 - fixed fragments from code, no input
        "SELECT user_id,"
        " public.brievie_sees_all_post(user_id) AS sees_all,"
        " public.brievie_my_post_ids(user_id) AS my_ids,"
        " public.brievie_actor_sorts_post(user_id) AS sorts"
        f" FROM public.workspaces_membership WHERE workspace_id = {_WORKSPACE}),"
        " c AS MATERIALIZED (SELECT public.brievie_covered_post_ids() AS covered)"
        " SELECT d.id, m.user_id FROM public.documents_document d"
        " CROSS JOIN m CROSS JOIN c"
        f" WHERE ({document_filter}) AND {condition}"
    )


def viewer_groups_sql(document_filter: str) -> str:
    """Who sees each group of alike letters: the preview's question, without a letter x member pass.

    Without reader rows, the switches decide a non-private letter's viewers from two
    things only: its `addressee_ids`, and its scanner while it is not done. Letters
    alike in both get the same viewers, so the condition runs once per group and member
    (a few thousand groups instead of every letter times every member). Rows:
    (addressee_ids, scanned_by_id, status, letters, sorted viewer ids); `scanned_by_id`
    is set only while not done, and `status` is "open" or "done" (the condition asks no
    more). A private letter's viewers come from its
    reader rows alone and do not depend on the switches, and a letter with an unexpired
    reader row is left out: ask `viewer_pairs_sql` about those one by one.

    `document_filter`: as `viewer_pairs_sql`. Run as the system.
    """
    if ";" in document_filter or "--" in document_filter or "/*" in document_filter:
        raise ValueError("document_filter must be one SQL fragment without comments.")
    condition = document_condition(
        user="m.user_id",
        sees_all="m.sees_all",
        my_ids="m.my_ids",
        sorts="m.sorts",
        covered="c.covered",
        table="g",
    )
    return (
        "WITH m AS MATERIALIZED ("  # noqa: S608 - fixed fragments from code, no input
        "SELECT user_id,"
        " public.brievie_sees_all_post(user_id) AS sees_all,"
        " public.brievie_my_post_ids(user_id) AS my_ids,"
        " public.brievie_actor_sorts_post(user_id) AS sorts"
        f" FROM public.workspaces_membership WHERE workspace_id = {_WORKSPACE}),"
        " c AS MATERIALIZED (SELECT public.brievie_covered_post_ids() AS covered),"
        " g AS (SELECT d.addressee_ids,"
        " CASE WHEN d.status <> 'done' THEN d.scanned_by_id END AS scanned_by_id,"
        " CASE WHEN d.status <> 'done' THEN 'open' ELSE 'done' END AS status,"
        " 'workspace'::text AS visibility, NULL::uuid AS id, pg_catalog.count(*) AS letters"
        " FROM public.documents_document d"
        f" WHERE ({document_filter}) AND d.visibility = 'workspace'"
        " AND NOT EXISTS (SELECT 1 FROM public.documents_documentreader r"
        " WHERE r.document_id = d.id"
        " AND (r.expires_at IS NULL OR r.expires_at > pg_catalog.now()))"
        " GROUP BY 1, 2, 3)"
        " SELECT g.addressee_ids, g.scanned_by_id, g.status, g.letters,"
        " COALESCE(pg_catalog.array_agg(m.user_id ORDER BY m.user_id)"
        f" FILTER (WHERE {condition}), '{{}}')"
        " FROM g CROSS JOIN m CROSS JOIN c"
        " GROUP BY g.addressee_ids, g.scanned_by_id, g.status, g.letters"
    )
