import re
from typing import Final

from django.db import migrations

from apps.workspaces.visibility_sql import POLICY_CONDITION

POLICY_NAME = "workspace_isolation"
SETTING_NAME = "brievie.workspace_id"

_TABLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def enable_rls(table: str) -> migrations.RunSQL:
    """Enable and force row-level security on a workspace table.

    Rows are visible and writable only when `brievie.workspace_id` equals their
    `workspace_id`. Without a context the setting is empty, NULLIF turns it into
    NULL and the comparison matches nothing, so code without a context sees zero
    rows. FORCE makes the policy bind the table owner as well.
    """
    if not _TABLE_NAME.fullmatch(table):
        raise ValueError(f"Not a plain table name: {table!r}")
    condition = f"workspace_id = NULLIF(current_setting('{SETTING_NAME}', true), '')::uuid"
    return migrations.RunSQL(
        sql=[
            f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
            f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
            f'CREATE POLICY {POLICY_NAME} ON "{table}"'
            f" USING ({condition}) WITH CHECK ({condition})",
        ],
        reverse_sql=[
            f'DROP POLICY {POLICY_NAME} ON "{table}"',
            f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY',
            f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY',
        ],
    )


ACTOR_SETTING = "brievie.actor"

# PB T1. `CASE` casts only a well-formed id (an OR has no evaluation order); both are
# SECURITY INVOKER with a pinned search_path, and EXECUTE stays granted to PUBLIC so the
# app role may call them.
ACTOR_FUNCTIONS_SQL: tuple[str, ...] = (
    f"""CREATE FUNCTION public.brievie_actor_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = ''
AS $$
  SELECT CASE
    WHEN pg_catalog.current_setting('{ACTOR_SETTING}', true)
         ~ '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
    THEN pg_catalog.current_setting('{ACTOR_SETTING}', true)::uuid
  END
$$""",
    f"""CREATE FUNCTION public.brievie_is_system() RETURNS boolean
LANGUAGE sql STABLE PARALLEL SAFE SET search_path = ''
AS $$
  SELECT COALESCE(pg_catalog.current_setting('{ACTOR_SETTING}', true), '') = 'system'
$$""",
)


def actor_functions() -> migrations.RunSQL:
    """`brievie_actor_id()` (a user actor's id, else NULL) and `brievie_is_system()`."""
    return migrations.RunSQL(
        sql=ACTOR_FUNCTIONS_SQL,
        reverse_sql=[
            "DROP FUNCTION public.brievie_actor_id()",
            "DROP FUNCTION public.brievie_is_system()",
        ],
    )


# The second layer (plan Task 7, S 4.9.3): restrictive policies that narrow what the
# workspace policy lets through. Restrictive policies are ANDed with the permissive
# `workspace_isolation`; INSERT (without RETURNING) checks only the latter.
VISIBILITY_POLICIES: Final = (
    "document_visibility",
    "document_visibility_update",
    "document_visibility_delete",
)
READER_POLICY: Final = "reader_scope"
_COMMANDS: Final = ("SELECT", "UPDATE", "DELETE")


def _checked(table: str) -> str:
    if not _TABLE_NAME.fullmatch(table):
        raise ValueError(f"Not a plain table name: {table!r}")
    return table


def _restrictive(table: str, condition: str) -> migrations.RunSQL:
    """The three restrictive policies with one condition. No WITH CHECK, deliberately:
    PostgreSQL then checks an UPDATE's new row with the same USING condition, so a write
    that would hide the row from the actor fails with 42501 instead of passing."""
    return migrations.RunSQL(
        sql=[
            f'CREATE POLICY {name} ON "{table}" AS RESTRICTIVE FOR {command} USING ({condition})'
            for name, command in zip(VISIBILITY_POLICIES, _COMMANDS, strict=True)
        ],
        reverse_sql=[f'DROP POLICY {name} ON "{table}"' for name in VISIBILITY_POLICIES],
    )


def document_visibility_policy() -> migrations.RunSQL:
    """Who sees, updates and deletes a letter: `visibility_sql.POLICY_CONDITION`."""
    return _restrictive("documents_document", POLICY_CONDITION)


def restrict_to_visible_documents(table: str, *, nullable: bool = False) -> migrations.RunSQL:
    """A row with a letter follows its letter: visible, updatable and deletable only when
    the letter is (the document policy runs inside the EXISTS)."""
    table = _checked(table)
    condition = (
        'EXISTS (SELECT 1 FROM "documents_document" d'  # noqa: S608 - checked table name
        f' WHERE d.id = "{table}".document_id)'
    )
    if nullable:
        condition = f'"{table}".document_id IS NULL OR {condition}'
    return _restrictive(table, condition)


def restrict_reader_rows() -> migrations.RunSQL:
    """A user sees their own reader rows; only the system writes them. No child policy on
    this table: the document policy reads it, and a policy back to the document would
    be a circle."""
    return migrations.RunSQL(
        sql=[
            f'CREATE POLICY {READER_POLICY} ON "documents_documentreader" AS RESTRICTIVE FOR ALL'
            " USING ((SELECT public.brievie_is_system())"
            " OR user_id = (SELECT public.brievie_actor_id()))"
            " WITH CHECK ((SELECT public.brievie_is_system()))"
        ],
        reverse_sql=[f'DROP POLICY {READER_POLICY} ON "documents_documentreader"'],
    )


# S 4.9.3: a task without a letter is seen by the system, whoever sees all post, its
# assignee and its maker (R39); a task on a letter follows the letter.
TASK_CONDITION: Final = (
    'CASE WHEN "tasks_task".document_id IS NULL THEN'
    " (SELECT public.brievie_is_system())"
    " OR (SELECT public.brievie_sees_all_post((SELECT public.brievie_actor_id())))"
    ' OR "tasks_task".assigned_to_id = (SELECT public.brievie_actor_id())'
    ' OR "tasks_task".created_by_id = (SELECT public.brievie_actor_id())'
    ' ELSE EXISTS (SELECT 1 FROM "documents_document" d WHERE d.id = "tasks_task".document_id)'
    " END"
)


def task_visibility_policy() -> migrations.RunSQL:
    return _restrictive("tasks_task", TASK_CONDITION)
