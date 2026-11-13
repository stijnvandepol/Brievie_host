from django.apps import apps
from django.db import connection

from apps.common.models import WorkspaceModel


def workspace_tables() -> list[str]:
    return [
        model._meta.db_table
        for model in apps.get_models()
        if issubclass(model, WorkspaceModel) and not model._meta.proxy
    ]


def test_there_are_workspace_tables_to_check() -> None:
    assert "documents_folder" in workspace_tables()


def test_every_workspace_table_has_forced_rls_and_the_isolation_policy() -> None:
    with connection.cursor() as c:
        for table in workspace_tables():
            c.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                [table],
            )
            assert c.fetchone() == (True, True), table
            c.execute(
                """
                SELECT polname,
                       polpermissive,
                       polcmd,
                       pg_get_expr(polqual, polrelid),
                       pg_get_expr(polwithcheck, polrelid)
                FROM pg_policy
                WHERE polrelid = %s::regclass
                """,
                [table],
            )
            policies = c.fetchall()
            # Exactly one policy: an extra permissive one (e.g. USING (true)) would widen access.
            assert len(policies) == 1, (table, policies)
            name, permissive, command, using, with_check = policies[0]
            assert name == "workspace_isolation", table
            assert permissive is True, table
            assert command == "*", table
            assert "brievie.workspace_id" in using, table
            assert "brievie.workspace_id" in with_check, table
