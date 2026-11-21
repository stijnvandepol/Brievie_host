"""Older clients never wipe the new settings (plan Task 31, review focus 5): a body without
the new fields leaves them as they are, and every answer still has what a released client
requires."""

import json
from pathlib import Path

from django.test import Client

from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.visibility_support import as_actor, home, office, private_letter

JSON = "application/json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RELEASED = ("openapi_1b.json", "openapi_1c.json")


def _required(schema_name: str) -> list[tuple[str, set[str]]]:
    found = []
    for fixture in RELEASED:
        schemas = json.loads((FIXTURES / fixture).read_text())["components"]["schemas"]
        if schema_name in schemas:
            found.append((fixture, set(schemas[schema_name].get("required", []))))
    return found


def test_old_settings_body_keeps_new_switches(client: Client) -> None:
    o = office()
    Workspace.objects.filter(id=o.workspace.id).update(care_is_private=True, emergency_access=True)
    client.force_login(o.admin)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/settings",
        {"allow_member_export": False},
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    stored = Workspace.objects.get(id=o.workspace.id)
    assert (
        stored.everyone_sees_all,
        stored.departments_enabled,
        stored.admins_read_all,
        stored.care_is_private,
        stored.emergency_access,
        stored.allow_member_export,
    ) == (False, True, False, True, True, False)


def test_old_person_body_keeps_a_department(client: Client) -> None:
    o = office()
    finance = o.persons["Finance"]
    client.force_login(o.admin)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{finance.id}",
        {"display_name": "Financiën", "name_variants": [], "archived": False},
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.get(id=finance.id).kind == Person.Kind.DEPARTMENT


def test_old_label_body_keeps_its_department(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        label = Label.objects.create(
            workspace=o.workspace, name="Finance: Facturen", department=o.persons["Finance"]
        )
    client.force_login(o.admin)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/labels/{label.id}",
        {"name": "Finance: Rekeningen", "color": "blue"},
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert Label.objects.get(id=label.id).department_id == o.persons["Finance"].id


def test_answers_validate_against_released_schemas(client: Client) -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client.force_login(h.bob)
    base = f"/api/v1/workspaces/{h.workspace.id}"
    answers = {
        "DocumentOut": [client.get(f"{base}/documents/{doc.id}").json()],
        "DocumentSummaryOut": client.get(f"{base}/documents").json(),
        "PersonOut": client.get(f"{base}/persons").json(),
        "LabelOut": client.get(f"{base}/labels").json(),
        "MemberOut": client.get(f"{base}/members").json(),
        "WorkspaceSettingsOut": [client.get(f"{base}/settings").json()],
    }
    checked = 0
    for name, rows in answers.items():
        assert rows, name
        for fixture, required in _required(name):
            for row in rows:
                missing = required - set(row)
                assert not missing, (name, fixture, missing)
                checked += 1
    assert checked
