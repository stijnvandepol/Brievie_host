"""The phone's calendar feed drops tasks of letters one can no longer see (S 4.10, plan
Task 16, Review Focus 5).

The iPhone app fills the phone's calendar from `listTasks` and removes an event whose
task no longer comes back (`CalendarSyncPlanner.plan`). So a task of a letter the member
lost sight of must leave `listTasks`, in the shape the released app decodes, and no item
may carry text of a letter the member cannot read. The ICS feed is not built.
"""

import json
import uuid
from pathlib import Path
from typing import Final

from django.test import Client

from apps.documents.models import Document
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.visibility_support import Office, as_actor, office

RELEASED: Final = Path(__file__).parent / "fixtures" / "openapi_1c.json"
_JSON_TYPES: Final[dict[str, type]] = {"string": str, "boolean": bool, "integer": int}


def _tasks(o: Office, client: Client) -> list[dict[str, object]]:
    response = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks?status=all")
    assert response.status_code == 200
    items: list[dict[str, object]] = response.json()
    return items


def _finance_task(o: Office) -> Task:
    with as_actor(o.workspace, SYSTEM):
        return Task.objects.get(document=o.letters["finance_letter"])


def test_hidden_letters_task_leaves_list_tasks(client: Client) -> None:
    o = office()
    task_id = str(_finance_task(o).id)
    client.force_login(o.finance)
    before = {str(item["id"]): item for item in _tasks(o, client)}
    assert task_id in before

    # Femke hands Finance's letter to HR: she no longer reads it.
    letter = o.letters["finance_letter"]
    response = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}/addressees",
        {"person_ids": [str(o.persons["HR"].id)]},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["can_read"] is False

    after = _tasks(o, client)
    assert task_id not in {str(item["id"]) for item in after}
    shapes = {frozenset(item) for item in [*before.values(), *after]}
    assert len(shapes) == 1, shapes  # every item, before and after, has the same fields

    client.force_login(o.hr)
    assert task_id in {str(item["id"]) for item in _tasks(o, client)}


def test_task_item_carries_no_text_of_a_hidden_letter(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=o.letters["finance_letter"].id).update(
            sender="Kikkerpoel Incasso"
        )
        Task.objects.filter(document=o.letters["finance_letter"]).update(
            description="Betaal de aanslag Zwaluwstaart"
        )
    for user in (o.hr, o.plain, o.sorter, o.admin, o.viewer):
        client.force_login(user)
        body = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks?status=all")
        text = body.content.decode()
        assert "Kikkerpoel" not in text, user.name
        assert "Zwaluwstaart" not in text, user.name
        assert str(o.letters["finance_letter"].id) not in text, user.name

    client.force_login(o.finance)
    text = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks?status=all").content.decode()
    assert "Kikkerpoel" in text
    assert "Zwaluwstaart" in text


def _matches(value: object, spec: dict[str, object]) -> bool:
    kinds = spec.get("type")
    names = kinds if isinstance(kinds, list) else [kinds]
    for name in names:
        if name == "null" and value is None:
            return True
        expected = _JSON_TYPES.get(str(name))
        if expected is not None and isinstance(value, expected):
            if spec.get("format") == "uuid":
                uuid.UUID(str(value))
            return True
    return False


def test_list_tasks_answer_matches_the_released_contract(client: Client) -> None:
    released = json.loads(RELEASED.read_text(encoding="utf-8"))
    schema = released["components"]["schemas"]["TaskItemOut"]
    o = office()
    client.force_login(o.finance)
    items = _tasks(o, client)
    assert items
    for item in items:
        for name in schema["required"]:
            assert name in item, name
        for name, spec in schema["properties"].items():
            assert _matches(item[name], spec), (name, item[name])
