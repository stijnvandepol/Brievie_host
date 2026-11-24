"""`listMyTasks` and `countMyTasks` (spec S 11, plan Task 6)."""

import json
from datetime import date
from pathlib import Path

from django.test import Client

from apps.common.management.commands import export_openapi
from apps.workspaces.models import Membership
from tests.documents_support import bearer_for
from tests.my_tasks_support import household, letter

FIXTURES = Path(__file__).parent / "fixtures"


def _url(workspace_id: object, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{workspace_id}/my-tasks{suffix}"


def test_list_my_tasks_shape(client: Client) -> None:
    h = household()
    task = letter(
        h.workspace,
        on=[h.persons["Anne"], h.persons["Piet"]],
        deadline=date(2030, 1, 1),
    )
    client.force_login(h.anne)
    r = client.get(_url(h.workspace.id))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "today", "has_person", "truncated", "waiting_letters", "split"}
    assert body["has_person"] is True
    assert body["split"] is False
    (item,) = body["items"]
    assert item["id"] == str(task.id)
    assert item["reason"] == "addressee"
    assert item["group"] == "later"
    assert item["for_names"] == ["Piet"]
    assert item["can_edit"] is True
    assert item["amount"] == ""
    assert item["needs_deadline"] is False


def test_count_my_tasks_shape(client: Client) -> None:
    h = household()
    letter(h.workspace, on=[h.persons["Anne"]], deadline=date(2000, 1, 1))
    letter(h.workspace, on=[h.persons["Anne"]], term_quote="binnen zes weken")
    client.force_login(h.anne)
    r = client.get(_url(h.workspace.id, "/counts"))
    assert r.status_code == 200
    assert r.json() == {
        "mine": 2,
        "overdue": 1,
        "within_week": 0,
        "needs_date": 1,
        "to_pick_up": 0,
        "waiting": 0,
        "snoozed": 0,
    }


def test_device_token_lists_and_counts() -> None:
    h = household()
    letter(h.workspace, on=[h.persons["Piet"]])
    headers = bearer_for(h.piet)
    device = Client(headers=headers)
    listed = device.get(_url(h.workspace.id))
    counted = device.get(_url(h.workspace.id, "/counts"))
    assert listed.status_code == counted.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert counted.json()["mine"] == 1


def test_list_tasks_unchanged_in_meaning(client: Client) -> None:
    """`listTasks` keeps every visible open task in its order; the new fields only add."""
    h = household()
    later = letter(h.workspace, on=[h.persons["Piet"]], deadline=date(2030, 1, 2))
    sooner = letter(h.workspace, deadline=date(2030, 1, 1))
    client.force_login(h.anne)
    r = client.get(f"/api/v1/workspaces/{h.workspace.id}/tasks")
    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [str(sooner.id), str(later.id)]
    for row in r.json():
        assert row["reason"] == ""
        assert row["for_names"] == []
        assert row["group"] == "later"


def test_contract_additive() -> None:
    """Against the released 1c contract: nothing removed, every new field defaulted or
    optional, and no `anyOf` anywhere."""
    released = json.loads((FIXTURES / "openapi_1c.json").read_text(encoding="utf-8"))
    current = json.loads(export_openapi.render_schema())
    assert "anyOf" not in json.dumps(current)
    for name in ("TaskItemOut", "TaskOut", "TaskIn"):
        old = released["components"]["schemas"][name]
        new = current["components"]["schemas"][name]
        assert set(old["properties"]) <= set(new["properties"]), name
        assert set(new.get("required", [])) <= set(old.get("required", [])), name
    operations = {
        op["operationId"]
        for item in current["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }
    assert {"listMyTasks", "countMyTasks"} <= operations


def test_bad_part_is_422(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    r = client.get(_url(h.workspace.id), {"part": "iemand"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_viewer_lists_but_cannot_edit(client: Client) -> None:
    h = household()
    letter(h.workspace)
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(role="viewer")
    client.force_login(h.piet)
    r = client.get(_url(h.workspace.id), {"part": "all"})
    assert r.status_code == 200
    assert [item["can_edit"] for item in r.json()["items"]] == [False]
