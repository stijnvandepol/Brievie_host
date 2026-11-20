"""Who sees which letter, decided by the database (plan Task 7, S 4.1 and 4.9.3).

The office of `tests.visibility_support` starts in state E off, D on, A off with Sandra
sorting post. Each test looks at it through the policies, as one actor.
"""

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import DatabaseError, IntegrityError, ProgrammingError, connection, transaction
from django.db.models import Model

from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentReader,
    DocumentShare,
    DocumentSuggestion,
    Extraction,
)
from apps.labels.models import Label
from apps.rules.models import RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import NOBODY, SYSTEM, Actor
from apps.workspaces.models import Workspace
from tests.documents_support import make_document
from tests.factories import UserFactory
from tests.visibility_support import ALL_16_STATES, LETTERS, Office, as_actor, office

fill = importlib.import_module("apps.documents.migrations.0012_visibility")
switches = importlib.import_module("apps.workspaces.migrations.0006_visibility_switches")

NOT_PRIVATE = set(LETTERS) - {"private_femke"}
UNCOVERED = {"unassigned", "only_directie", "person_without_account"}


@pytest.fixture
def o(db: None) -> Office:
    return office()


def test_plain_member_sees_only_own_post(o: Office) -> None:
    assert o.visible(o.plain.id) == {"own_piet", "scanned_in_review"}
    assert o.visible(o.viewer.id) == set()


def test_department_member_sees_department_post(o: Office) -> None:
    assert o.visible(o.finance.id) == {"finance_letter", "scanned_in_review", "private_femke"}
    assert o.visible(o.hr.id) == {"hr_letter"}


def test_sorter_sees_uncovered_post(o: Office) -> None:
    assert o.visible(o.sorter.id) == UNCOVERED


def test_admin_without_admins_read_all_sorts_only_when_nobody_is_flagged(o: Office) -> None:
    assert o.visible(o.admin.id) == set()
    o.state(e=False, d=True, a=False, s=False)
    assert o.visible(o.admin.id) == UNCOVERED
    assert o.visible(o.sorter.id) == set()


def test_everyone_sees_all_except_private(o: Office) -> None:
    o.state(e=True, d=True, a=False)
    for name, user in o.users.items():
        expected = NOT_PRIVATE | ({"private_femke"} if name == "finance" else set())
        assert o.visible(user.id) == expected, name


def test_admins_read_all_shows_admins_non_private_post(o: Office) -> None:
    o.state(e=False, d=True, a=True)
    assert o.visible(o.admin.id) == NOT_PRIVATE
    assert o.visible(o.plain.id) == {"own_piet", "scanned_in_review"}


def test_departments_off_send_department_post_to_sorters(o: Office) -> None:
    o.state(e=False, d=False, a=False)
    assert o.visible(o.sorter.id) == UNCOVERED | {"finance_letter", "hr_letter"}
    assert o.visible(o.finance.id) == {"scanned_in_review", "private_femke"}
    assert o.visible(o.hr.id) == set()


def test_scanner_sees_letter_until_done(o: Office) -> None:
    letter = o.letters["scanned_in_review"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=letter.id).update(status="done")
    assert o.visible(o.plain.id) == {"own_piet"}


def test_empty_actor_sees_no_letter(o: Office) -> None:
    o.state(e=True, d=True, a=True)
    assert o.visible(NOBODY) == set()


def test_non_member_actor_sees_no_letter(o: Office) -> None:
    o.state(e=True, d=True, a=True)
    assert o.visible(UserFactory.create().id) == set()


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_private_letter_only_by_reader_rows_in_every_state(
    o: Office, state: dict[str, bool]
) -> None:
    o.state(**state)
    for name, user in o.users.items():
        assert ("private_femke" in o.visible(user.id)) is (name == "finance"), name
    assert "private_femke" in o.visible(SYSTEM)


CHILD_MODELS: list[type[Model]] = [
    DocumentPage,
    Extraction,
    DocumentAddressee,
    DocumentLabel,
    DocumentEvent,
    DocumentSuggestion,
    DocumentShare,
    RuleRun,
    Task,
]


@pytest.mark.parametrize("model", CHILD_MODELS)
def test_child_rows_follow_their_document(o: Office, model: type[Model]) -> None:
    manager = model._default_manager
    with as_actor(o.workspace, SYSTEM):
        rows = {(i, d) for i, d in manager.values_list("id", "document_id")}
    assert rows
    actors: list[Actor] = [*(u.id for u in o.users.values()), NOBODY]
    for actor in actors:
        letters = {o.letters[n].id for n in o.visible(actor)}
        with as_actor(o.workspace, actor):
            seen = set(manager.values_list("id", "document_id"))
        assert seen == {(i, d) for i, d in rows if d in letters}, actor


def _letterless_task(o: Office, **fields: object) -> Task:
    with as_actor(o.workspace, SYSTEM):
        return Task.objects.create(
            workspace=o.workspace, type="other", description="Zonder brief", **fields
        )


def _sees_task(o: Office, actor: Actor, task: Task) -> bool:
    with as_actor(o.workspace, actor):
        return Task.objects.filter(id=task.id).exists()


def test_letterless_task_visible_to_maker_assignee_and_sees_all(o: Office) -> None:
    task = _letterless_task(o, created_by=o.plain, assigned_to=o.hr)
    seen = {name for name, user in o.users.items() if _sees_task(o, user.id, task)}
    assert seen == {"plain", "hr"}
    o.state(e=False, d=True, a=True)
    seen = {name for name, user in o.users.items() if _sees_task(o, user.id, task)}
    assert seen == {"plain", "hr", "admin"}
    o.state(e=True, d=True, a=False)
    assert all(_sees_task(o, user.id, task) for user in o.users.values())


def test_letterless_task_without_maker_hidden_once_everyone_is_off(o: Office) -> None:
    task = _letterless_task(o)
    assert not any(_sees_task(o, user.id, task) for user in o.users.values())
    assert _sees_task(o, SYSTEM, task)
    assert not _sees_task(o, NOBODY, task)
    o.state(e=True, d=True, a=False)
    assert all(_sees_task(o, user.id, task) for user in o.users.values())


def test_migrated_database_shows_every_member_what_it_showed(o: Office) -> None:
    # S 4.16: the rows exist before the migration; its data steps run on them.
    with as_actor(o.workspace, SYSTEM):
        Document.objects.update(addressee_ids=[])
    Workspace.objects.filter(id=o.workspace.id).update(
        everyone_sees_all=False, departments_enabled=True, admins_read_all=False
    )
    switches.visibility_defaults(global_apps, connection.schema_editor())
    # In the migration the document policies do not exist yet; here they do.
    with as_actor(o.workspace, SYSTEM):
        fill.fill_addressee_ids(global_apps, connection.schema_editor())

    for name, user in o.users.items():
        expected = NOT_PRIVATE | ({"private_femke"} if name == "finance" else set())
        assert o.visible(user.id) == expected, name


def test_fill_addressee_ids_matches_the_links(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        Document.objects.update(addressee_ids=[])
        fill.fill_addressee_ids(global_apps, connection.schema_editor())
    with as_actor(o.workspace, SYSTEM):
        for name, letter in o.letters.items():
            links = sorted(
                DocumentAddressee.objects.filter(document=letter).values_list(
                    "person_id", flat=True
                )
            )
            stored = Document.objects.get(id=letter.id).addressee_ids
            assert stored == links, name


def test_the_unassigned_index_is_partial_on_letters_without_an_addressee() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'documents_document_unassigned'"
        )
        row = c.fetchone()
    assert row is not None
    definition = row[0]
    assert "(workspace_id, created_at DESC)" in definition
    assert "(addressee_ids = '{}'::uuid[])" in definition
    assert "((status)::text = 'done'::text)" in definition


# The write tests of PB T3, against the office: Piet does not see `finance_letter`.


def _sqlstate(error: BaseException) -> str | None:
    cause = error.__cause__
    return getattr(cause, "sqlstate", None)


def test_update_of_a_hidden_letter_touches_nothing(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id):
        changed = Document.objects.filter(id=o.letters["finance_letter"].id).update(sender="x")
    assert changed == 0


def test_update_without_where_leaves_hidden_letters_alone(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id), connection.cursor() as c:
        c.execute("UPDATE documents_document SET sender = 'x'")
        assert c.rowcount == 2
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.get(id=o.letters["finance_letter"].id).sender == ""


def test_queryset_update_without_filter_leaves_hidden_tasks_alone(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id):
        Task.objects.update(description="x")
    with as_actor(o.workspace, SYSTEM):
        hidden = Task.objects.get(document=o.letters["finance_letter"])
    assert hidden.description == "Betalen"


def test_delete_without_where_leaves_hidden_children_alone(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id), connection.cursor() as c:
        c.execute("DELETE FROM documents_documentlabel")
    with as_actor(o.workspace, SYSTEM):
        assert DocumentLabel.objects.filter(document=o.letters["finance_letter"]).exists()
        assert not DocumentLabel.objects.filter(document=o.letters["own_piet"]).exists()


def test_delete_of_hidden_children_deletes_nothing(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id):
        result = DocumentLabel.objects.filter(document_id=o.letters["finance_letter"].id).delete()
    assert result == (0, {})


def test_select_for_update_does_not_return_a_hidden_letter(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id):
        found = (
            Document.objects.select_for_update(no_key=True)
            .filter(id=o.letters["finance_letter"].id)
            .first()
        )
    assert found is None


def test_save_with_update_fields_of_a_hidden_letter_raises(o: Office) -> None:
    letter = o.letters["finance_letter"]
    letter.sender = "x"
    with (
        as_actor(o.workspace, o.plain.id),
        pytest.raises(DatabaseError, match="did not affect any rows"),
    ):
        letter.save(update_fields=["sender"])


def test_refresh_of_a_hidden_letter_raises_does_not_exist(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id), pytest.raises(Document.DoesNotExist):
        o.letters["finance_letter"].refresh_from_db()


def test_update_that_hides_the_row_from_the_actor_is_42501(o: Office) -> None:
    with (
        as_actor(o.workspace, o.plain.id),
        pytest.raises(ProgrammingError) as caught,
        transaction.atomic(),
    ):
        Document.objects.filter(id=o.letters["own_piet"].id).update(visibility="restricted")
    assert _sqlstate(caught.value) == "42501"


def test_flip_without_where_by_a_non_reader_is_42501(o: Office) -> None:
    with (
        as_actor(o.workspace, o.plain.id),
        pytest.raises(ProgrammingError) as caught,
        transaction.atomic(),
        connection.cursor() as c,
    ):
        c.execute("UPDATE documents_document SET visibility = 'restricted'")
    assert _sqlstate(caught.value) == "42501"


def test_making_private_after_writing_own_reader_row_succeeds(o: Office) -> None:
    letter = o.letters["own_piet"]
    with as_actor(o.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=o.workspace, document=letter, user=o.plain, reason="addressee"
        )
    with as_actor(o.workspace, o.plain.id):
        assert Document.objects.filter(id=letter.id).update(visibility="restricted") == 1


def test_making_public_by_a_reader_succeeds(o: Office) -> None:
    with as_actor(o.workspace, o.finance.id):
        changed = Document.objects.filter(id=o.letters["private_femke"].id).update(
            visibility="workspace"
        )
    assert changed == 1


def test_user_sees_only_own_reader_rows(o: Office) -> None:
    with as_actor(o.workspace, o.finance.id):
        assert DocumentReader.objects.count() == 1
    with as_actor(o.workspace, o.plain.id):
        assert DocumentReader.objects.count() == 0
    with as_actor(o.workspace, SYSTEM):
        assert DocumentReader.objects.count() == 1


def test_user_cannot_write_reader_rows(o: Office) -> None:
    with (
        as_actor(o.workspace, o.finance.id),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        DocumentReader.objects.create(
            workspace=o.workspace,
            document=o.letters["finance_letter"],
            user=o.finance,
            reason="share",
        )


def test_child_update_to_point_at_a_hidden_letter_is_42501(o: Office) -> None:
    with (
        as_actor(o.workspace, o.plain.id),
        pytest.raises(ProgrammingError) as caught,
        transaction.atomic(),
    ):
        DocumentLabel.objects.filter(document=o.letters["own_piet"]).update(
            document_id=o.letters["finance_letter"].id
        )
    assert _sqlstate(caught.value) == "42501"


def test_child_insert_on_a_hidden_letter_passes_the_workspace_check(o: Office) -> None:
    with as_actor(o.workspace, o.plain.id):
        DocumentEvent(
            workspace=o.workspace,
            document_id=o.letters["finance_letter"].id,
            kind=DocumentEvent.Kind.LABEL_ADDED,
        ).save()
    with as_actor(o.workspace, SYSTEM):
        assert DocumentEvent.objects.filter(document=o.letters["finance_letter"]).count() == 2


def test_child_pointing_into_another_workspace_still_raises(o: Office, ws_a: Workspace) -> None:
    other = make_document(ws_a)
    with as_actor(o.workspace, o.plain.id), pytest.raises(ValueError, match="same workspace"):
        DocumentEvent(
            workspace=o.workspace, document_id=other.id, kind=DocumentEvent.Kind.LABEL_ADDED
        ).save()


def test_fk_check_ignores_rls_so_a_user_delete_fails_at_commit(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        label = Label.objects.create(workspace=o.workspace, name="Los")
        DocumentLabel.objects.create(
            workspace=o.workspace, document=o.letters["finance_letter"], label=label
        )
    with as_actor(o.workspace, o.plain.id):
        with connection.cursor() as c:
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(IntegrityError), transaction.atomic():
            label.delete()


POLICY_TABLES = (
    "documents_document",
    "documents_documentpage",
    "documents_extraction",
    "documents_documentaddressee",
    "documents_documentlabel",
    "documents_documentevent",
    "documents_documentsuggestion",
    "documents_documentshare",
    "documents_documentreader",
    "rules_rulerun",
    "tasks_task",
)


@pytest.mark.parametrize("table", POLICY_TABLES)
def test_no_policy_recursion(o: Office, table: str) -> None:
    with as_actor(o.workspace, o.plain.id), connection.cursor() as c:
        c.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608 - fixed table names
        c.execute(f'UPDATE "{table}" SET updated_at = updated_at WHERE false')  # noqa: S608
        c.execute(f'DELETE FROM "{table}" WHERE false')  # noqa: S608


def test_another_workspace_sees_none_of_it(o: Office, ws_a: Workspace) -> None:
    with as_actor(ws_a, SYSTEM):
        assert not Document.objects.filter(id__in=[d.id for d in o.letters.values()]).exists()
