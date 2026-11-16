"""Every code path locks a workspace's rules in id order, and before a label or person.

The executor holds rule locks and then needs the target label or person; a deletion that
locked the label or person first, or the rules in another order, could deadlock with it.
"""

from collections.abc import Callable

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.labels import services as label_services
from apps.labels.models import Label
from apps.people import services as people_services
from apps.rules import services as rule_services
from apps.rules.models import Rule
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import make_person
from tests.rules_support import add_label, make_rule, text_word


def locks(workspace: Workspace, call: Callable[[], object]) -> list[str]:
    """The SQL of every locking query `call` runs."""
    with workspace_context(workspace.id), CaptureQueriesContext(connection) as queries:
        call()
    return [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]


def rule_lock_index(sql: list[str]) -> int:
    [index] = [i for i, q in enumerate(sql) if 'FROM "rules_rule"' in q]
    assert 'ORDER BY "rules_rule"."id" ASC' in sql[index]
    return index


def test_reorder_locks_the_rules_in_id_order(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        ids = list(Rule.objects.filter(workspace=ws_a).values_list("id", flat=True))

    sql = locks(ws_a, lambda: rule_services.reorder_rules(workspace=ws_a, rule_ids=ids[::-1]))

    rule_lock_index(sql)


def test_delete_label_locks_the_rules_first_in_id_order(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        own = Label.objects.create(workspace=ws_a, name="Eigen")

    sql = locks(ws_a, lambda: label_services.delete_label(workspace=ws_a, label_id=own.id))

    [label_lock] = [i for i, q in enumerate(sql) if 'FROM "labels_label"' in q]
    assert rule_lock_index(sql) < label_lock


def test_delete_person_locks_the_rules_first_in_id_order(ws_a: Workspace) -> None:
    make_rule(ws_a, text_word("APK"), actions=[add_label(_any_label(ws_a))])
    person = make_person(ws_a, "Oud")

    sql = locks(ws_a, lambda: people_services.delete_person(workspace=ws_a, person_id=person.id))

    [person_lock] = [i for i, q in enumerate(sql) if 'FROM "people_person"' in q]
    assert rule_lock_index(sql) < person_lock


def _any_label(workspace: Workspace) -> Label:
    with workspace_context(workspace.id):
        label = Label.objects.filter(workspace=workspace).first()
    assert label is not None
    return label
