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
