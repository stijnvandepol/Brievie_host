"""Grants, forgetting a member, and the private-post seams (plan Task 24, PB T8)."""

from datetime import timedelta

from django.utils import timezone
from pytest_django import DjangoAssertNumQueries

from apps.documents import visibility
from apps.documents.models import DocumentEvent, DocumentReader, DocumentShare
from apps.people.models import DepartmentMember, Person
from apps.workspaces import access
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.visibility_support import as_actor, home, letter, private_letter, readers


def test_has_private_post_counts_only_restricted_letters() -> None:
    h = home()
    letter(h.workspace, on=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        assert not visibility.has_private_post(h.persons["Bob"])
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        assert visibility.has_private_post(h.persons["Bob"])
        assert not visibility.has_private_post(h.persons["Carla"])


def test_reads_private_post_ignores_emergency_and_expired_rows() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=h.workspace,
            document=doc,
            user=h.anne,
            reason=DocumentReader.Reason.EMERGENCY,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        DocumentReader.objects.create(
            workspace=h.workspace,
            document=doc,
            user=h.carla,
            reason=DocumentReader.Reason.SHARE,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        assert visibility.reads_private_post(h.bob.id)
        assert not visibility.reads_private_post(h.anne.id)
        assert not visibility.reads_private_post(h.carla.id)


def test_a_share_on_a_letter_that_is_not_private_is_no_private_post() -> None:
    h = home()
    doc = letter(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        DocumentShare.objects.create(workspace=h.workspace, document=doc, user=h.carla)
        visibility.recompute(doc)
        assert not visibility.reads_private_post(h.carla.id)


def test_private_post_readers_is_one_query(
    django_assert_max_num_queries: DjangoAssertNumQueries,
) -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]], shared_with=[h.carla])
    # One query for the readers; system_actor adds its savepoint and reads, sets and
    # restores the actor around it.
    with as_actor(h.workspace, SYSTEM), django_assert_max_num_queries(6):
        found = visibility.private_post_readers()
    assert found == {h.bob.id, h.carla.id}


def test_forget_member_removes_rows_shares_and_department_memberships_in_that_workspace_only() -> (
    None
):
    h = home()
    other_admin = h.anne
    other = create_workspace(name="Werk", kind="business", admin=other_admin)
    Membership.objects.create(user=h.bob, workspace=other, role=Membership.Role.MEMBER)
    mine = private_letter(h.workspace, shared_with=[h.bob], scanned_by=h.carla)
    theirs = private_letter(other, shared_with=[h.bob], scanned_by=h.anne)
    with as_actor(h.workspace, SYSTEM):
        group = Person.objects.create(
            workspace=h.workspace, display_name="Ouders", kind=Person.Kind.DEPARTMENT
        )
        DepartmentMember.objects.create(workspace=h.workspace, department=group, user=h.bob)
    visibility_before = readers(theirs)
    with as_actor(h.workspace, SYSTEM):
        visibility.forget_member(h.workspace.id, h.bob.id)
        assert not DepartmentMember.objects.filter(user=h.bob).exists()
        assert not DocumentShare.objects.filter(user=h.bob).exists()
    assert ("Bob", "share") not in readers(mine)
    assert readers(theirs) == visibility_before
    assert ("Bob", "share") in readers(theirs)


def test_record_grant_writes_a_history_line_on_each_private_letter() -> None:
    h = home()
    first = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    second = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    letter(h.workspace, on=[h.persons["Oma"]])
    with as_actor(h.workspace, SYSTEM):
        count = visibility.record_grant(h.persons["Oma"], grantee=h.carla, actor_user_id=h.anne.id)
    assert count == 2
    for doc in (first, second):
        with as_actor(h.workspace, h.bob.id):
            (line,) = DocumentEvent.objects.filter(document=doc, kind="access_granted")
        assert (line.actor_id, line.person_name, line.note) == (h.anne.id, "Oma", "Carla")
        with as_actor(h.workspace, h.anne.id):
            assert not DocumentEvent.objects.filter(document=doc).exists()


def test_person_requires_email_when_the_person_has_private_post() -> None:
    h = home()
    with as_actor(h.workspace, h.anne.id):
        assert not access.person_requires_email(h.persons["Oma"])
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        assert access.person_requires_email(h.persons["Oma"])


def test_on_member_added_makes_the_linked_person_a_reader_and_records_the_grant() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    newcomer = UserFactory.create(name="Opa")
    Membership.objects.create(user=newcomer, workspace=h.workspace, role=Membership.Role.MEMBER)
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Oma"].id).update(user=newcomer)
    with as_actor(h.workspace, newcomer.id):
        access.on_member_added(
            h.workspace.id, newcomer.id, person_id=h.persons["Oma"].id, created_by_id=h.anne.id
        )
    assert ("Opa", "addressee") in readers(doc)
    with as_actor(h.workspace, newcomer.id):
        (line,) = DocumentEvent.objects.filter(document=doc, kind="access_granted")
    assert line.actor_id == h.anne.id


def test_on_member_removed_forgets_the_member() -> None:
    h = home()
    doc = private_letter(h.workspace, shared_with=[h.carla], scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        access.on_member_removed(h.workspace.id, h.carla.id)
    assert ("Carla", "share") not in readers(doc)


def test_seams_work_outside_any_workspace_context() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    assert access.reads_private_post(h.workspace.id, h.bob.id) is True
    assert access.reads_private_post(h.workspace.id, h.carla.id) is False
    assert access.private_post_readers(h.workspace.id) == {h.bob.id}
