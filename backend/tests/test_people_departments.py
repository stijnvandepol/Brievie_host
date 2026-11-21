"""Departments: members, leads, archiving and matching (plan Task 25, PB T11, S 4.5)."""

from typing import TYPE_CHECKING

from django.test import Client
from django.utils import timezone

from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.documents import visibility
from apps.documents.models import Document, DocumentEvent
from apps.people.matching import match_addressees
from apps.people.models import DepartmentMember, Person
from apps.pipeline import steps
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.visibility_support import (
    Home,
    as_actor,
    home,
    letter,
    office,
    private_letter,
    readers,
    sees,
)

if TYPE_CHECKING:
    from tests.helpers import Response

JSON = "application/json"


def _persons_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/persons"


def _members_url(person: Person, user_id: object = None) -> str:
    url = f"/api/v1/workspaces/{person.workspace_id}/persons/{person.id}/members"
    return url if user_id is None else f"{url}/{user_id}"


def _department(h: Home, name: str = "Ouders", variants: tuple[str, ...] = ()) -> Person:
    with as_actor(h.workspace, SYSTEM):
        return Person.objects.create(
            workspace=h.workspace,
            display_name=name,
            name_variants=list(variants),
            kind=Person.Kind.DEPARTMENT,
        )


def _add(client: Client, department: Person, user_id: object, **extra: bool) -> "Response":
    return client.post(
        _members_url(department), {"user_id": str(user_id), **extra}, content_type=JSON
    )


def _audit(workspace: Workspace, code: str) -> list[WorkspaceAuditEvent]:
    with as_actor(workspace, SYSTEM):
        return list(WorkspaceAuditEvent.objects.filter(code=code).order_by("created_at", "id"))


def test_create_department_and_add_members(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.post(
        _persons_url(h.workspace), {"display_name": "Ouders", "kind": "department"}, JSON
    )
    assert r.status_code == 201, r.json()
    assert r.json()["kind"] == "department"
    assert r.json()["member_user_ids"] == []
    with as_actor(h.workspace, SYSTEM):
        department = Person.objects.get(id=r.json()["id"])
    r = _add(client, department, h.bob.id)
    assert r.status_code == 200, r.json()
    assert r.json()["member_user_ids"] == [str(h.bob.id)]
    assert r.json()["lead_user_ids"] == []
    listed = {p["id"]: p for p in client.get(_persons_url(h.workspace)).json()}
    assert listed[str(department.id)]["member_user_ids"] == [str(h.bob.id)]
    assert listed[str(h.persons["Bob"].id)]["kind"] == "person"


def test_only_an_admin_makes_a_department(client: Client) -> None:
    h = home()
    client.force_login(h.bob)
    r = client.post(
        _persons_url(h.workspace), {"display_name": "Ouders", "kind": "department"}, JSON
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_an_unknown_kind_is_422(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.post(_persons_url(h.workspace), {"display_name": "X", "kind": "robot"}, JSON)
    assert r.status_code == 422


def test_department_cannot_be_linked_to_an_account(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.anne)
    r = client.put(
        f"{_persons_url(h.workspace)}/{department.id}/member",
        {"user_id": str(h.bob.id)},
        content_type=JSON,
    )
    assert r.status_code == 409
    assert r.json()["code"] == "is_a_department"


def test_a_person_has_no_members(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = _add(client, h.persons["Oma"], h.bob.id)
    assert r.status_code == 409
    assert r.json()["code"] == "not_a_department"


def test_member_cannot_change_a_department(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.bob)
    assert _add(client, department, h.bob.id).status_code == 403


def test_a_device_token_cannot_change_a_department(client: Client) -> None:
    from tests.members_support import bearer, paired

    h = home()
    department = _department(h)
    _device, token = paired(h.anne)
    r = client.post(
        _members_url(department),
        {"user_id": str(h.bob.id)},
        content_type=JSON,
        headers=bearer(token),
    )
    assert r.status_code == 401


def test_adding_a_non_member_is_404(client: Client) -> None:
    from tests.factories import UserFactory

    h = home()
    department = _department(h)
    client.force_login(h.anne)
    r = _add(client, department, UserFactory.create().id)
    assert r.status_code == 404
    assert r.json()["code"] == "member_not_found"


def test_private_letter_for_department_is_read_by_its_members() -> None:
    h = home()
    department = _department(h)
    with as_actor(h.workspace, SYSTEM):
        DepartmentMember.objects.create(workspace=h.workspace, department=department, user=h.bob)
    doc = private_letter(h.workspace, addressees=[department])
    assert readers(doc) == {("Bob", "department")}
    assert sees(h.bob, doc)
    assert not sees(h.carla, doc)


def test_readers_after_department_member_added_and_removed(client: Client) -> None:
    h = home()
    department = _department(h)
    doc = private_letter(h.workspace, addressees=[department], scanned_by=h.bob)
    client.force_login(h.anne)
    assert _add(client, department, h.carla.id).status_code == 200
    assert ("Carla", "department") in readers(doc)
    r = client.delete(_members_url(department, h.carla.id))
    assert r.status_code == 200, r.json()
    assert r.json()["member_user_ids"] == []
    assert ("Carla", "department") not in readers(doc)
    assert [a.values for a in _audit(h.workspace, "department_member_removed")] == [
        {"member": "Carla"}
    ]


def test_admin_cannot_add_self_to_department_with_private_post(client: Client) -> None:
    h = home()
    department = _department(h)
    doc = private_letter(h.workspace, addressees=[department], scanned_by=h.bob)
    client.force_login(h.anne)
    r = _add(client, department, h.anne.id)
    assert r.status_code == 409
    assert r.json()["code"] == "cannot_grant_self"
    assert ("Anne", "department") not in readers(doc)
    with as_actor(h.workspace, SYSTEM):
        assert not DepartmentMember.objects.filter(user=h.anne).exists()


def test_admin_may_add_self_to_department_without_private_post(client: Client) -> None:
    h = home()
    department = _department(h)
    letter(h.workspace, on=[department])
    client.force_login(h.anne)
    r = _add(client, department, h.anne.id)
    assert r.status_code == 200
    assert r.json()["member_user_ids"] == [str(h.anne.id)]


def test_admin_may_remove_self_from_a_department_and_loses_its_letters(client: Client) -> None:
    h = home()
    department = _department(h)
    with as_actor(h.workspace, SYSTEM):
        DepartmentMember.objects.create(workspace=h.workspace, department=department, user=h.anne)
    doc = private_letter(h.workspace, addressees=[department])
    assert sees(h.anne, doc)
    client.force_login(h.anne)
    r = client.delete(_members_url(department, h.anne.id))
    assert r.status_code == 200
    assert not sees(h.anne, doc)


def test_grant_writes_audit_and_history_on_each_private_letter(client: Client) -> None:
    h = home()
    department = _department(h)
    with as_actor(h.workspace, SYSTEM):
        DepartmentMember.objects.create(workspace=h.workspace, department=department, user=h.bob)
    doc = private_letter(h.workspace, addressees=[department])
    client.force_login(h.anne)
    assert _add(client, department, h.carla.id).status_code == 200
    with as_actor(h.workspace, h.bob.id):
        (line,) = DocumentEvent.objects.filter(document=doc, kind="access_granted")
    assert (line.note, line.actor_id) == ("Carla", h.anne.id)
    with as_actor(h.workspace, h.anne.id):
        assert not DocumentEvent.objects.filter(document=doc).exists()
    assert [a.values for a in _audit(h.workspace, "department_member_added")] == [
        {"member": "Carla"}
    ]


def test_a_department_member_counts_as_a_reader_for_automatic_make_private() -> None:
    h = home()
    department = _department(h)
    with as_actor(h.workspace, SYSTEM):
        DepartmentMember.objects.create(workspace=h.workspace, department=department, user=h.bob)
    doc = letter(h.workspace, on=[department])
    with as_actor(h.workspace, SYSTEM):
        result = visibility.make_private(doc, actor=AuditActor.system(), origin="rule")
    assert result == "made_private"
    assert readers(doc) == {("Bob", "department")}


def test_name_matching_and_rules_work_for_a_department() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Finance"].id).update(name_variants=["Afd. Finance"])
        persons = steps.active_persons()
    matches = match_addressees("T.a.v. Afd. Finance\nFactuur 12", persons)
    assert o.persons["Finance"].id in {m.person_id for m in matches}


def test_matcher_ignores_departments_while_they_are_off() -> None:
    o = office()
    o.state(e=False, d=False, a=False)
    with as_actor(o.workspace, SYSTEM):
        ids = {p.person_id for p in steps.active_persons()}
    assert o.persons["Finance"].id not in ids
    assert o.persons["Piet"].id in ids


def test_update_person_without_kind_keeps_a_department(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.anne)
    r = client.put(
        f"{_persons_url(h.workspace)}/{department.id}", {"display_name": "Kinderen"}, JSON
    )
    assert r.status_code == 200, r.json()
    assert r.json()["kind"] == "department"


def test_update_with_another_kind_changes_nothing(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.anne)
    r = client.put(
        f"{_persons_url(h.workspace)}/{department.id}",
        {"display_name": "Ouders", "kind": "person"},
        JSON,
    )
    assert r.status_code == 200
    assert r.json()["kind"] == "department"


def test_deleting_an_empty_department_removes_its_memberships(client: Client) -> None:
    h = home()
    department = _department(h)
    with as_actor(h.workspace, SYSTEM):
        DepartmentMember.objects.create(workspace=h.workspace, department=department, user=h.bob)
    client.force_login(h.anne)
    r = client.delete(f"{_persons_url(h.workspace)}/{department.id}")
    assert r.status_code == 204
    with as_actor(h.workspace, SYSTEM):
        assert not DepartmentMember.objects.filter(user=h.bob).exists()


def test_deleting_a_department_with_letters_is_in_use(client: Client) -> None:
    h = home()
    department = _department(h)
    letter(h.workspace, on=[department])
    client.force_login(h.anne)
    r = client.delete(f"{_persons_url(h.workspace)}/{department.id}")
    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"


def test_lead_is_set_and_audited(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.anne)
    assert _add(client, department, h.bob.id, is_lead=True).json()["lead_user_ids"] == [
        str(h.bob.id)
    ]
    r = client.put(_members_url(department, h.bob.id), {"is_lead": False}, content_type=JSON)
    assert r.status_code == 200, r.json()
    assert r.json()["lead_user_ids"] == []
    r = client.put(_members_url(department, h.bob.id), {"is_lead": True}, content_type=JSON)
    assert r.json()["lead_user_ids"] == [str(h.bob.id)]
    rows = [a.values for a in _audit(h.workspace, "department_lead_changed")]
    assert rows == [{"member": "Bob", "value": False}, {"member": "Bob", "value": True}]
    # The same value again writes nothing.
    client.put(_members_url(department, h.bob.id), {"is_lead": True}, content_type=JSON)
    assert len(_audit(h.workspace, "department_lead_changed")) == 2


def test_lead_of_somebody_outside_the_department_is_404(client: Client) -> None:
    h = home()
    department = _department(h)
    client.force_login(h.anne)
    r = client.put(_members_url(department, h.bob.id), {"is_lead": True}, content_type=JSON)
    assert r.status_code == 404
    assert r.json()["code"] == "member_not_found"


def test_archived_department_is_not_proposed_and_not_assignable(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    client.force_login(o.admin)
    r = client.put(
        f"{_persons_url(o.workspace)}/{finance.id}",
        {"display_name": "Finance", "archived": True},
        JSON,
    )
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert finance.id not in {p.person_id for p in steps.active_persons()}
    doc = o.letters["unassigned"]
    client.force_login(o.sorter)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/documents/{doc.id}/addressees",
        {"person_ids": [str(finance.id)]},
        content_type=JSON,
    )
    assert r.status_code == 422


def test_archived_department_members_keep_seeing_its_letters() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Finance"].id).update(archived_at=timezone.now())
    assert "finance_letter" in o.visible(o.finance.id)


def test_department_on_several_letters_with_two_departments() -> None:
    o = office()
    both = letter(o.workspace, on=[o.persons["Finance"], o.persons["HR"]])
    assert sees(o.finance, both)
    assert sees(o.hr, both)
    assert not sees(o.plain, both)
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.get(id=both.id).addressee_ids == sorted(
            [o.persons["Finance"].id, o.persons["HR"].id]
        )
