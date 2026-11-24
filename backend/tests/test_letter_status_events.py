"""The history lines of the letter status (plan Task 6, spec S 4.3, 12): the day a line
names, lines that cannot be undone, nothing written when a date passes."""

from datetime import date, datetime

import pytest
from django.test import Client

from apps.documents import organise
from apps.documents.models import DocumentEvent
from apps.tasks import services
from apps.workspaces.context import SYSTEM, workspace_context
from tests.helpers import post_json
from tests.letter_status_support import T, kinds, letter_with_tasks
from tests.my_tasks_support import household, membership


def _events_url(workspace_id: object, document_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/documents/{document_id}/events"


def test_event_out_carries_on_date(client: Client) -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with workspace_context(h.workspace.id, actor=h.anne.id):
        organise.record_task_waiting(document, actor=h.anne, until=date(2026, 12, 3))
        organise.record_task_waiting(document, actor=h.anne, until=None)
    client.force_login(h.anne)
    rows = client.get(_events_url(h.workspace.id, document.id)).json()
    by_kind = {row["kind"]: row for row in rows}
    assert by_kind["task_waiting"]["on_date"] == "2026-12-03"
    assert by_kind["task_waiting"]["actor_name"] == "Anne"
    assert by_kind["task_waiting_ended"]["on_date"] is None


@pytest.mark.parametrize(
    "kind", ["task_waiting", "task_waiting_ended", "letter_handled", "letter_reopened"]
)
def test_new_kinds_not_undoable(client: Client, kind: str) -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        event = DocumentEvent.objects.create(
            workspace=h.workspace, document=document, kind=kind, origin="user", actor=h.anne
        )
    client.force_login(h.anne)
    r = post_json(
        client, f"/workspaces/{h.workspace.id}/documents/{document.id}/events/{event.id}/undo"
    )
    assert r.status_code == 409
    assert r.json()["code"] == "event_not_undoable"
    rows = client.get(_events_url(h.workspace.id, document.id)).json()
    assert [row["can_undo"] for row in rows] == [False]


def test_no_line_when_waiting_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    document, _tasks = letter_with_tasks(
        h.workspace, [T(waiting_until=date(2026, 12, 3))], on=[h.persons["Anne"]]
    )
    before = kinds(document.id, h.workspace)

    def later(now: datetime | None = None) -> date:
        return date(2026, 12, 10)

    monkeypatch.setattr(services, "task_today", later)
    with workspace_context(h.workspace.id, actor=h.anne.id):
        services.count_my_tasks(membership(h.workspace, h.anne))
        services.my_tasks(membership(h.workspace, h.anne))
    assert kinds(document.id, h.workspace) == before == []
