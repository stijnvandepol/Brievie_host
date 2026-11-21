"""Labels of a department (plan Task 26, S 4.6, R42)."""

from django.test import Client

from apps.documents.models import DocumentLabel
from apps.labels.models import Label
from apps.people.models import DepartmentMember
from apps.workspaces.context import SYSTEM
from tests.visibility_support import Office, as_actor, office

JSON = "application/json"


def _url(o: Office, label: Label | None = None) -> str:
    base = f"/api/v1/workspaces/{o.workspace.id}/labels"
    return base if label is None else f"{base}/{label.id}"


def _finance_label(o: Office, name: str = "Finance: Facturen") -> Label:
    with as_actor(o.workspace, SYSTEM):
        return Label.objects.create(
            workspace=o.workspace, name=name, department=o.persons["Finance"]
        )


def _listed(client: Client, o: Office) -> set[str]:
    r = client.get(_url(o))
    assert r.status_code == 200
    return {row["name"] for row in r.json()}


def _make_lead(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        DepartmentMember.objects.filter(user=o.finance).update(is_lead=True)


def test_department_label_offered_to_members_and_admins_only(client: Client) -> None:
    o = office()
    label = _finance_label(o)
    for user, offered in ((o.finance, True), (o.admin, True), (o.hr, False), (o.plain, False)):
        client.force_login(user)
        assert (label.name in _listed(client, o)) is offered, user.name
    client.force_login(o.admin)
    (row,) = [r for r in client.get(_url(o)).json() if r["name"] == label.name]
    assert row["department_id"] == str(o.persons["Finance"].id)


def test_department_label_listed_for_everyone_while_departments_are_off(client: Client) -> None:
    o = office()
    label = _finance_label(o)
    o.state(e=False, d=False, a=False)
    client.force_login(o.hr)
    assert label.name in _listed(client, o)


def test_lead_creates_a_label_for_their_department(client: Client) -> None:
    o = office()
    _make_lead(o)
    client.force_login(o.finance)
    r = client.post(
        _url(o),
        {"name": "Finance: Bonnen", "department_id": str(o.persons["Finance"].id)},
        content_type=JSON,
    )
    assert r.status_code == 201, r.json()
    assert r.json()["department_id"] == str(o.persons["Finance"].id)


def test_member_cannot_create_a_department_label(client: Client) -> None:
    o = office()
    client.force_login(o.finance)
    r = client.post(
        _url(o),
        {"name": "Finance: Bonnen", "department_id": str(o.persons["Finance"].id)},
        content_type=JSON,
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_a_lead_of_another_department_cannot_either(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        DepartmentMember.objects.filter(user=o.hr).update(is_lead=True)
    client.force_login(o.hr)
    r = client.post(
        _url(o), {"name": "X", "department_id": str(o.persons["Finance"].id)}, content_type=JSON
    )
    assert r.status_code == 403


def test_a_person_is_no_department_for_a_label(client: Client) -> None:
    o = office()
    client.force_login(o.admin)
    r = client.post(
        _url(o), {"name": "X", "department_id": str(o.persons["Piet"].id)}, content_type=JSON
    )
    assert r.status_code == 422


def test_member_cannot_rename_or_delete_a_department_label(client: Client) -> None:
    o = office()
    label = _finance_label(o)
    client.force_login(o.finance)
    assert client.put(_url(o, label), {"name": "Anders"}, content_type=JSON).status_code == 403
    assert client.delete(_url(o, label)).status_code == 403


def test_member_still_manages_labels_for_everyone(client: Client) -> None:
    o = office()
    client.force_login(o.plain)
    r = client.post(_url(o), {"name": "Eigen"}, content_type=JSON)
    assert r.status_code == 201
    assert r.json()["department_id"] is None


def test_admin_moves_a_label_to_a_department_and_back(client: Client) -> None:
    o = office()
    client.force_login(o.admin)
    created = client.post(_url(o), {"name": "Salaris"}, content_type=JSON).json()
    with as_actor(o.workspace, SYSTEM):
        label = Label.objects.get(id=created["id"])
    finance = str(o.persons["Finance"].id)
    r = client.put(_url(o, label), {"name": "Salaris", "department_id": finance}, JSON)
    assert r.json()["department_id"] == finance
    r = client.put(_url(o, label), {"name": "Salaris"}, content_type=JSON)
    assert r.json()["department_id"] == finance
    r = client.put(_url(o, label), {"name": "Salaris", "department_id": None}, JSON)
    assert r.json()["department_id"] is None


def test_department_label_grants_nothing(client: Client) -> None:
    o = office()
    label = _finance_label(o)
    with as_actor(o.workspace, SYSTEM):
        DocumentLabel.objects.create(
            workspace=o.workspace, document=o.letters["hr_letter"], label=label
        )
    assert "hr_letter" not in o.visible(o.finance.id)
    client.force_login(o.hr)
    doc = o.letters["hr_letter"]
    r = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents/{doc.id}")
    assert str(label.id) in r.json()["label_ids"]


def test_deleting_the_department_keeps_the_label_for_everyone(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        from apps.people.models import Person

        empty = Person.objects.create(
            workspace=o.workspace, display_name="Inkoop", kind=Person.Kind.DEPARTMENT
        )
        label = Label.objects.create(
            workspace=o.workspace, name="Inkoop: bestellingen", department=empty
        )
    client.force_login(o.admin)
    assert client.delete(f"/api/v1/workspaces/{o.workspace.id}/persons/{empty.id}").status_code == (
        204
    )
    with as_actor(o.workspace, SYSTEM):
        assert Label.objects.get(id=label.id).department_id is None
    client.force_login(o.plain)
    assert "Inkoop: bestellingen" in _listed(client, o)
