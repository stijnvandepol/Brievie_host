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
                """
                SELECT c.relrowsecurity,
                       c.relforcerowsecurity,
                       EXISTS (
                           SELECT 1 FROM pg_policy p
                           WHERE p.polrelid = c.oid AND p.polname = 'workspace_isolation'
                       )
                FROM pg_class c
                WHERE c.oid = %s::regclass
                """,
                [table],
            )
            assert c.fetchone() == (True, True, True), table
