from django.apps import apps
from django.db import connection

from apps.common.models import WorkspaceModel
from apps.documents.models import Document
from apps.workspaces.rls import READER_POLICY, VISIBILITY_POLICIES


def workspace_tables() -> list[str]:
    return [
        model._meta.db_table
        for model in apps.get_models()
        if issubclass(model, WorkspaceModel) and not model._meta.proxy
    ]


def test_there_are_workspace_tables_to_check() -> None:
    assert "documents_folder" in workspace_tables()


def _policies(table: str) -> list[tuple[str, bool, str, str, str | None]]:
    with connection.cursor() as c:
        c.execute(
            """
            SELECT polname,
                   polpermissive,
                   polcmd,
                   pg_get_expr(polqual, polrelid),
                   pg_get_expr(polwithcheck, polrelid)
            FROM pg_policy
            WHERE polrelid = %s::regclass
            ORDER BY polname
            """,
            [table],
        )
        return list(c.fetchall())


def test_every_workspace_table_has_forced_rls_and_the_isolation_policy() -> None:
    with connection.cursor() as c:
        for table in workspace_tables():
            c.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                [table],
            )
            assert c.fetchone() == (True, True), table


def test_every_workspace_table_has_exactly_one_permissive_policy() -> None:
    for table in workspace_tables():
        permissive = [p for p in _policies(table) if p[1]]
        # An extra permissive policy (e.g. USING (true)) would widen access.
        assert len(permissive) == 1, (table, permissive)
        name, _permissive, command, using, with_check = permissive[0]
        assert (name, command) == ("workspace_isolation", "*"), table
        assert "brievie.workspace_id" in using, table
        assert with_check is not None
        assert "brievie.workspace_id" in with_check, table


KNOWN_RESTRICTIVE = {
    VISIBILITY_POLICIES[0]: "r",
    VISIBILITY_POLICIES[1]: "w",
    VISIBILITY_POLICIES[2]: "d",
    READER_POLICY: "*",
}


def test_other_policies_are_known_restrictive_ones() -> None:
    for table in workspace_tables():
        for name, permissive, command, _using, with_check in _policies(table):
            if name == "workspace_isolation":
                continue
            assert not permissive, (table, name)
            assert KNOWN_RESTRICTIVE.get(name) == command, (table, name, command)
            if name in VISIBILITY_POLICIES:
                assert with_check is None, (table, name)


def _tables_with_a_document_key_in_django() -> set[str]:
    return {
        model._meta.db_table
        for model in apps.get_models()
        for field in model._meta.concrete_fields
        if field.is_relation and field.related_model is Document
    }


def test_every_table_with_a_document_foreign_key_has_the_visibility_policies() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT DISTINCT conrelid::regclass::text FROM pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'documents_document'::regclass"
        )
        in_database = {row[0].strip('"') for row in c.fetchall()}
    tables = in_database - {"documents_documentreader"}
    assert tables == _tables_with_a_document_key_in_django() - {"documents_documentreader"}
    for table in tables:
        policies = {p[0]: p for p in _policies(table)}
        for name in VISIBILITY_POLICIES:
            assert name in policies, (table, name)
            _name, permissive, _command, using, _check = policies[name]
            assert not permissive, (table, name)
            assert "documents_document" in using, (table, name)


def test_document_policies_read_the_reader_table_and_the_helpers() -> None:
    policies = {p[0]: p for p in _policies("documents_document")}
    for name in VISIBILITY_POLICIES:
        using = policies[name][3]
        assert "documents_documentreader" in using, name
        for helper in (
            "brievie_is_system",
            "brievie_actor_id",
            "brievie_sees_all_post",
            "brievie_my_post_ids",
            "brievie_actor_sorts_post",
            "brievie_covered_post_ids",
        ):
            assert helper in using, (name, helper)


def test_reader_table_has_reader_scope_and_no_child_policy() -> None:
    names = [p[0] for p in _policies("documents_documentreader")]
    assert names == [READER_POLICY, "workspace_isolation"]


def test_task_policies_handle_tasks_without_a_letter() -> None:
    policies = {p[0]: p for p in _policies("tasks_task")}
    for name in VISIBILITY_POLICIES:
        using = policies[name][3]
        assert "document_id IS NULL" in using, name
        assert "created_by_id" in using, name
        assert "assigned_to_id" in using, name
