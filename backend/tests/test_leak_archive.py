"""Every new read and write of the archive under the rights rules (archive plan Task 11,
spec S 8, S 13), in the office of "Wie ziet wat": E off, departments on, a private letter
and a private own document, a manager, departments, a viewer; every member as the actor."""

import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents import member_export, reminders, services, visibility
from apps.documents.models import Document
from apps.documents.search import SearchFilters, search_documents
from apps.documents.services import ArchiveFilters
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import membership_of, own_document, private_own_document
from tests.documents_support import trashed
from tests.my_tasks_support import ids, mine
from tests.visibility_support import Office, as_actor, office

TODAY = timezone.localdate()
SECRET_TITLE = "Kopie paspoort van de huurder"


class _World:
    def __init__(self) -> None:
        o = office()
        self.o = o
        # Femke's private passport: only she (and its uploader, Femke) reads it.
        self.private = private_own_document(
            o.workspace,
            on=[o.persons["Femke"]],
            scanned_by=o.finance,
            title=SECRET_TITLE,
            document_type="identity",
        )
        with as_actor(o.workspace, SYSTEM):
            Document.objects.filter(id=self.private.id).update(
                expires_on=TODAY + timedelta(days=20)
            )
        # A contract of the Finance department, not private.
        self.finance = own_document(
            o.workspace,
            on=[o.persons["Finance"]],
            scanned_by=o.finance,
            title="Leasecontract kopieerapparaat",
            document_type="contract",
            expires_on=TODAY + timedelta(days=10),
        )
        # Piet's own diploma.
        self.piet = own_document(
            o.workspace,
            on=[o.persons["Piet"]],
            scanned_by=o.plain,
            title="Diploma bakkerij",
            document_type="diploma",
        )
        reminders.start_expiry_reminders(today=TODAY)


@pytest.fixture
def w() -> _World:
    return _World()


def _visible_done(o: Office, user: User) -> set[uuid.UUID]:
    member = membership_of(o.workspace, user)
    with as_actor(o.workspace, user.id):
        return set(
            visibility.visible_documents(member).filter(status="done").values_list("id", flat=True)
        )


def _archive(o: Office, user: User, *, expiring: bool = False) -> set[uuid.UUID]:
    member = membership_of(o.workspace, user)
    filters = ArchiveFilters(archived=True, expiring=expiring)
    with as_actor(o.workspace, user.id):
        return {d.id for d in services.list_documents(member, archive=filters)}


def test_archive_list_and_facets_show_only_what_the_actor_may_read(w: _World) -> None:
    o = w.o
    for user in o.users.values():
        seen = _visible_done(o, user)
        assert _archive(o, user) == seen, user.name
        member = membership_of(o.workspace, user)
        with as_actor(o.workspace, user.id):
            facets = services.list_archive_facets(member)
        assert sum(f.count for f in facets.kinds) == len(seen), user.name


def test_a_hidden_documents_title_or_reminder_never_appears(w: _World, client: Client) -> None:
    o = w.o
    for user in (o.admin, o.sorter, o.hr, o.plain, o.viewer):
        assert w.private.id not in _archive(o, user), user.name
        assert w.private.id not in _archive(o, user, expiring=True), user.name
        member = membership_of(o.workspace, user)
        with as_actor(o.workspace, user.id):
            found = search_documents(
                member, q="paspoort huurder", filters=SearchFilters(), limit=30, offset=0
            )
            rows = services.document_summaries_out(
                member, services.list_documents(member, archive=ArchiveFilters(archived=True))
            )
        assert w.private.id not in {hit.document.id for hit in found.hits}, user.name
        assert all(SECRET_TITLE not in row.title for row in rows), user.name
        tasks = mine(o.workspace, user, part="all")
        assert all(SECRET_TITLE not in item.task.description for item in tasks.items), user.name
        assert w.private.id not in member_export.admin_document_ids(o.workspace.id, user.id)
        client.force_login(user)
        response = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents/archive-facets")
        assert response.status_code == 200
        assert SECRET_TITLE not in response.content.decode()


def test_private_own_document_is_seen_only_by_its_readers_and_the_uploader(w: _World) -> None:
    o = w.o
    o.state(e=True, d=True, a=True)
    for user in o.users.values():
        expected = user == o.finance
        assert (w.private.id in _archive(o, user)) is expected, user.name


def test_document_for_a_department_is_seen_by_its_members_only_when_the_rules_say(
    w: _World,
) -> None:
    o = w.o
    assert w.finance.id in _archive(o, o.finance)
    for user in (o.hr, o.plain, o.viewer):
        assert w.finance.id not in _archive(o, user), user.name
    # Everyone sees all post: the department's contract too, as a letter would be.
    o.state(e=True, d=True, a=False)
    assert w.finance.id in _archive(o, o.hr)


def test_reminder_task_follows_the_task_policy(w: _World) -> None:
    o = w.o
    with as_actor(o.workspace, SYSTEM):
        private_reminder = Task.objects.get(document=w.private, type=Task.Type.EXPIRY)
        finance_reminder = Task.objects.get(document=w.finance, type=Task.Type.EXPIRY)
    assert private_reminder.id in ids(mine(o.workspace, o.finance, part="all"))
    assert finance_reminder.id in ids(mine(o.workspace, o.finance, part="all"))
    for user in (o.admin, o.sorter, o.hr, o.plain, o.viewer):
        seen = ids(mine(o.workspace, user, part="all"))
        assert private_reminder.id not in seen, user.name
        assert finance_reminder.id not in seen, user.name


def test_details_update_on_a_hidden_document_is_404(w: _World, client: Client) -> None:
    o = w.o
    for user in (o.admin, o.sorter, o.hr, o.plain):
        client.force_login(user)
        response = client.patch(
            f"/api/v1/workspaces/{o.workspace.id}/documents/{w.private.id}/details",
            {"title": "Anders"},
            content_type="application/json",
        )
        assert response.status_code == 404, user.name
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.get(id=w.private.id).title == SECRET_TITLE


def test_export_scopes_do_not_leak_across_actors(w: _World) -> None:
    o = w.o
    for state in ({"e": False, "d": True, "a": False}, {"e": True, "d": True, "a": True}):
        o.state(**state)
        everything = set(member_export.admin_document_ids(o.workspace.id, o.admin.id))
        assert w.private.id not in everything
        assert o.letters["private_femke"].id not in everything
        for user in o.users.values():
            own = set(member_export.member_document_ids(o.workspace.id, user.id))
            assert own <= _visible_all(o, user), user.name
            if user != o.finance:
                assert w.private.id not in own, user.name


def _visible_all(o: Office, user: User) -> set[uuid.UUID]:
    with as_actor(o.workspace, user.id):
        return set(Document.objects.values_list("id", flat=True))


def test_trashed_documents_are_out_of_every_archive_read(w: _World) -> None:
    o = w.o
    trashed(w.piet)
    member = membership_of(o.workspace, o.plain)
    assert w.piet.id not in _archive(o, o.plain)
    with as_actor(o.workspace, o.plain.id):
        facets = services.list_archive_facets(member)
        found = search_documents(
            member, q="bakkerij", filters=SearchFilters(kind="document"), limit=30, offset=0
        )
    assert "diploma" not in {f.value for f in facets.types}
    assert found.hits == []
    assert w.piet.id not in member_export.member_document_ids(o.workspace.id, o.plain.id)
