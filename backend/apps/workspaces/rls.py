import re

from django.db import migrations

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
