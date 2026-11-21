"""The read marks (navigation plan Task 1): one row per member and letter, under forced
row-level security, the letter's policies and an own-row policy, so no member ever sees
another member's read state; the settings of "Blijft liggen" by kind; `unread_from`."""

import uuid

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.documents.models import DocumentReadMark
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from apps.workspaces.rls import READ_MARK_POLICY, VISIBILITY_POLICIES
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter

TABLE = "documents_documentreadmark"


def _mark(workspace_id: uuid.UUID, document_id: uuid.UUID, user_id: uuid.UUID) -> DocumentReadMark:
    return DocumentReadMark.objects.create(
        workspace_id=workspace_id, document_id=document_id, user_id=user_id, read_at=timezone.now()
    )


def test_read_mark_table_has_forced_rls_and_own_row_policy() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            [TABLE],
        )
        assert c.fetchone() == (True, True)
        c.execute(
            "SELECT polname, polpermissive, polcmd, pg_get_expr(polqual, polrelid),"
            " pg_get_expr(polwithcheck, polrelid) FROM pg_policy"
            " WHERE polrelid = %s::regclass ORDER BY polname",
            [TABLE],
        )
        policies = {row[0]: row[1:] for row in c.fetchall()}
    assert set(policies) == {READ_MARK_POLICY, "workspace_isolation", *VISIBILITY_POLICIES}
    permissive, command, using, with_check = policies[READ_MARK_POLICY]
    assert not permissive
    assert command == "*"
    for condition in (using, with_check):
        assert "user_id" in condition
        assert "brievie_actor_id" in condition
        assert "brievie_is_system" in condition


def test_member_sees_only_own_marks() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, SYSTEM):
        _mark(h.workspace.id, document.id, h.anne.id)
        _mark(h.workspace.id, document.id, h.bas.id)
    with as_actor(h.workspace, h.bas.id):
        assert list(DocumentReadMark.objects.values_list("user_id", flat=True)) == [h.bas.id]
        # Not even a count of the others.
        assert DocumentReadMark.objects.filter(user_id=h.anne.id).count() == 0
    with as_actor(h.workspace, h.anne.id):
        assert list(DocumentReadMark.objects.values_list("user_id", flat=True)) == [h.anne.id]


def test_member_cannot_insert_mark_for_someone_else() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, h.bas.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            _mark(h.workspace.id, document.id, h.anne.id)
        _mark(h.workspace.id, document.id, h.bas.id)
    with as_actor(h.workspace, SYSTEM):
        assert set(DocumentReadMark.objects.values_list("user_id", flat=True)) == {h.bas.id}


def test_member_cannot_move_or_delete_someone_elses_mark() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, SYSTEM):
        mark = _mark(h.workspace.id, document.id, h.anne.id)
    with as_actor(h.workspace, h.bas.id):
        assert DocumentReadMark.objects.filter(id=mark.id).delete()[0] == 0
        assert DocumentReadMark.objects.filter(id=mark.id).update(user_id=h.bas.id) == 0
    with as_actor(h.workspace, SYSTEM):
        assert DocumentReadMark.objects.get(id=mark.id).user_id == h.anne.id


def test_system_sees_all_marks_of_the_workspace_only() -> None:
    h = household()
    other = create_workspace(name="Elders", kind="home", admin=UserFactory.create())
    mine = letter(h.workspace, on=[h.persons["Piet"]])
    theirs = letter(other, on=[])
    with as_actor(h.workspace, SYSTEM):
        _mark(h.workspace.id, mine.id, h.anne.id)
        _mark(h.workspace.id, mine.id, h.piet.id)
    with as_actor(other, SYSTEM):
        _mark(other.id, theirs.id, other.memberships.get().user_id)
    with as_actor(h.workspace, SYSTEM):
        assert DocumentReadMark.objects.count() == 2
    with as_actor(other, SYSTEM):
        assert DocumentReadMark.objects.count() == 1


def test_a_member_does_not_see_own_mark_of_a_letter_no_longer_seen() -> None:
    # The letter's policies hold on the marks too: a mark says nothing of a hidden letter.
    from tests.visibility_support import office

    o = office()
    piet_letter = o.letters["own_piet"]
    with as_actor(o.workspace, SYSTEM):
        _mark(o.workspace.id, piet_letter.id, o.hr.id)
    with as_actor(o.workspace, o.hr.id):
        assert not DocumentReadMark.objects.exists()


def test_new_members_start_unread_now() -> None:
    before = timezone.now()
    h = household()
    for membership in Membership.objects.filter(workspace=h.workspace):
        assert membership.unread_from >= before


def test_business_workspaces_get_lingering_on_home_off() -> None:
    home = create_workspace(name="Thuis", kind="home", admin=UserFactory.create())
    business = create_workspace(name="Kantoor", kind="business", admin=UserFactory.create())
    assert home.lingering_enabled is False
    assert business.lingering_enabled is True
    for workspace in (home, business):
        assert (workspace.lingering_inbox_days, workspace.lingering_unopened_days) == (2, 5)
