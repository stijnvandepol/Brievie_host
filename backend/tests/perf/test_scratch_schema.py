"""The scratch schema measures what ships (P2). Statements as text; no database."""

from django.apps import apps

from apps.documents.models import Document
from apps.workspaces.visibility_sql import POLICY_CONDITION, visibility_functions_sql
from tests.perf import scratch_schema
from tests.perf.candidates import NO_ADDRESSEE


def test_the_document_policies_use_the_shipping_condition() -> None:
    statements = scratch_schema.statements()
    on_documents = [s for s in statements if 'ON "documents_document" AS RESTRICTIVE' in s]
    assert [s.split(" FOR ")[1].split(" ")[0] for s in on_documents] == [
        "SELECT",
        "UPDATE",
        "DELETE",
    ]
    for statement in on_documents:
        assert statement.endswith(f"USING ({POLICY_CONDITION})")
        assert "WITH CHECK" not in statement
    for function in visibility_functions_sql():
        assert function in statements


def test_the_reader_table_has_forced_row_level_security() -> None:
    statements = scratch_schema.statements()
    table = "documents_documentreader"
    assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in statements
    assert any(s.startswith(f'CREATE POLICY workspace_isolation ON "{table}"') for s in statements)


def test_every_table_with_a_document_key_follows_its_document() -> None:
    with_key = {
        m._meta.db_table
        for m in apps.get_models()
        for f in m._meta.concrete_fields
        if f.is_relation and f.related_model is Document
    }
    assert with_key - {"tasks_task"} == set(scratch_schema.CHILD_TABLES)
    statements = scratch_schema.statements()
    for table in (*scratch_schema.CHILD_TABLES, "tasks_task"):
        policies = [s for s in statements if f'ON "{table}" AS RESTRICTIVE' in s]
        assert len(policies) == 3, table
    assert "created_by_id = (SELECT public.brievie_actor_id())" in scratch_schema.TASK_CONDITION


def test_the_unassigned_index_matches_the_candidate_filter() -> None:
    (index,) = [s for s in scratch_schema.statements() if "documents_document_unassigned" in s]
    assert index.endswith("WHERE status = 'done' AND addressee_ids = '{}'")
    assert NO_ADDRESSEE.endswith("\"addressee_ids\" = '{}'::uuid[]")
