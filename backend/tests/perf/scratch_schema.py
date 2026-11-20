"""The visibility schema of S 4.9 on top of today's tables, for the measurement only (P2).

`install` adds to the migrated test database what Tasks 6 and 7 will add as migrations:
the switches, the sorting flag, `Person.kind`, `Document.visibility` and `addressee_ids`
(GIN), `Task.created_by`, the department-member and reader tables (forced RLS through the
real `enable_rls`), the four visibility functions and the policy condition from
`apps.workspaces.visibility_sql` (the SQL that ships), the reader policy, the three
restrictive document policies, the child policies and the task CASE policy. The actor
helpers already come from migration `workspaces/0005` (Task 3). A failing measurement
therefore costs no product migration. Removed in Task 11, when the measurement runs on the
real schema.

Run as the schema owner (the test role): it owns the tables and, being neither superuser
nor BYPASSRLS, is bound by the forced policies exactly as the app role is.
"""

from typing import Final

from django.db.backends.utils import CursorWrapper

from apps.workspaces.rls import enable_rls
from apps.workspaces.visibility_sql import POLICY_CONDITION, visibility_functions_sql

COLUMNS: Final = [
    "ALTER TABLE workspaces_workspace"
    " ADD COLUMN everyone_sees_all boolean NOT NULL DEFAULT true,"
    " ADD COLUMN departments_enabled boolean NOT NULL DEFAULT false,"
    " ADD COLUMN admins_read_all boolean NOT NULL DEFAULT true",
    "ALTER TABLE workspaces_membership ADD COLUMN sorts_post boolean NOT NULL DEFAULT false",
    "ALTER TABLE people_person ADD COLUMN kind varchar(20) NOT NULL DEFAULT 'person'",
    "ALTER TABLE documents_document"
    " ADD COLUMN visibility varchar(20) NOT NULL DEFAULT 'workspace',"
    " ADD COLUMN addressee_ids uuid[] NOT NULL DEFAULT '{}'",
    "CREATE INDEX documents_document_addressees ON documents_document USING gin (addressee_ids)",
    # Proposed with the candidates (Task 7 if the measurement bears it out): the letters
    # without an addressee, newest first, for the "Zonder naam" list and its count.
    "CREATE INDEX documents_document_unassigned ON documents_document"
    " (workspace_id, created_at DESC) WHERE status = 'done' AND addressee_ids = '{}'",
    # The R39 arm of the task policy reads the maker (Task 6 adds it as a field).
    "ALTER TABLE tasks_task ADD COLUMN created_by_id uuid NULL"
    " REFERENCES accounts_user (id) DEFERRABLE INITIALLY DEFERRED",
    "CREATE INDEX tasks_task_created_by_id ON tasks_task (created_by_id)",
]

# As Django would create them for the models of Tasks 6 and 7: foreign keys deferred,
# an index per foreign key, the unique constraints and the reader index of PB T3.
TABLES: Final = [
    """CREATE TABLE people_departmentmember (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  workspace_id uuid NOT NULL REFERENCES workspaces_workspace (id)
    DEFERRABLE INITIALLY DEFERRED,
  department_id uuid NOT NULL REFERENCES people_person (id) DEFERRABLE INITIALLY DEFERRED,
  user_id uuid NOT NULL REFERENCES accounts_user (id) DEFERRABLE INITIALLY DEFERRED,
  is_lead boolean NOT NULL,
  CONSTRAINT people_departmentmember_unique UNIQUE (department_id, user_id)
)""",
    "CREATE INDEX people_departmentmember_workspace_id ON people_departmentmember (workspace_id)",
    "CREATE INDEX people_departmentmember_department_id ON people_departmentmember (department_id)",
    "CREATE INDEX people_departmentmember_user_id ON people_departmentmember (user_id)",
    """CREATE TABLE documents_documentreader (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  workspace_id uuid NOT NULL REFERENCES workspaces_workspace (id)
    DEFERRABLE INITIALLY DEFERRED,
  document_id uuid NOT NULL REFERENCES documents_document (id) DEFERRABLE INITIALLY DEFERRED,
  user_id uuid NOT NULL REFERENCES accounts_user (id) DEFERRABLE INITIALLY DEFERRED,
  reason varchar(20) NOT NULL,
  expires_at timestamptz NULL,
  CONSTRAINT documents_documentreader_unique UNIQUE (document_id, user_id, reason)
)""",
    "CREATE INDEX documents_documentreader_workspace_id ON documents_documentreader (workspace_id)",
    "CREATE INDEX documents_documentreader_document_id ON documents_documentreader (document_id)",
    "CREATE INDEX documents_reader_user_document"
    " ON documents_documentreader (user_id, document_id)",
]

# PB T3's reader policy.
READER_POLICY: Final = (
    'CREATE POLICY reader_scope ON "documents_documentreader" AS RESTRICTIVE FOR ALL'
    " USING ((SELECT public.brievie_is_system())"
    " OR user_id = (SELECT public.brievie_actor_id()))"
    " WITH CHECK ((SELECT public.brievie_is_system()))"
)

# Every table with a document foreign key except the reader table (PB T3: no circle);
# tasks_task has its own CASE policy below.
CHILD_TABLES: Final = (
    "documents_documentpage",
    "documents_extraction",
    "documents_documentaddressee",
    "documents_documentlabel",
    "documents_documentevent",
    "documents_documentsuggestion",
    "rules_rulerun",
)

_COMMANDS: Final = (
    ("document_visibility", "SELECT"),
    ("document_visibility_update", "UPDATE"),
    ("document_visibility_delete", "DELETE"),
)

# S 4.9.3: tasks without a letter.
TASK_CONDITION: Final = (
    "CASE WHEN document_id IS NULL THEN"
    " (SELECT public.brievie_is_system())"
    " OR (SELECT public.brievie_sees_all_post((SELECT public.brievie_actor_id())))"
    " OR assigned_to_id = (SELECT public.brievie_actor_id())"
    " OR created_by_id = (SELECT public.brievie_actor_id())"
    ' ELSE EXISTS (SELECT 1 FROM "documents_document" d WHERE d.id = "tasks_task".document_id)'
    " END"
)


def _restrictive(table: str, condition: str) -> list[str]:
    """The three restrictive policies, no WITH CHECK (PB's PostgreSQL facts)."""
    return [
        f'CREATE POLICY {name} ON "{table}" AS RESTRICTIVE FOR {command} USING ({condition})'
        for name, command in _COMMANDS
    ]


def statements() -> list[str]:
    """Everything `install` runs, in order."""
    sql = [*COLUMNS, *TABLES]
    for table in ("people_departmentmember", "documents_documentreader"):
        operation = enable_rls(table).sql
        assert isinstance(operation, list)
        sql += [str(s) for s in operation]
    sql += visibility_functions_sql()
    sql.append(READER_POLICY)
    sql += _restrictive("documents_document", POLICY_CONDITION)
    for table in CHILD_TABLES:
        sql += _restrictive(
            table,
            f'EXISTS (SELECT 1 FROM "documents_document" d WHERE d.id = "{table}".document_id)',  # noqa: S608
        )
    sql += _restrictive("tasks_task", TASK_CONDITION)
    return sql


def install(cursor: CursorWrapper) -> None:
    for statement in statements():
        cursor.execute(statement)
