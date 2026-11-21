"""Emergency access (plan Task 33, PB T20, S 4.7): an hour, read-only, every letter on the
name the admin cannot see, private or not; logged, in the history, and told."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import AccountNotice
from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.documents import visibility
from apps.documents.models import (
    Document,
    DocumentEvent,
    DocumentReader,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.people.models import DepartmentMember, Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.visibility_support import (
    Home,
    as_actor,
    home,
    office,
    private_letter,
    readers,
)

if TYPE_CHECKING:
    from tests.helpers import Response

JSON = "application/json"
REASON = "Bob ligt in het ziekenhuis, de rekening moet betaald"


def _on(h: Home) -> None:
    Workspace.objects.filter(id=h.workspace.id).update(emergency_access=True)


def _open(client: Client, person: Person, reason: str = REASON) -> "Response":
    return client.post(
        f"/api/v1/workspaces/{person.workspace_id}/persons/{person.id}/emergency-access",
        {"reason": reason},
        content_type=JSON,
    )


def _get(client: Client, doc: Document) -> int:
    return client.get(f"/api/v1/workspaces/{doc.workspace_id}/documents/{doc.id}").status_code


def _admin(h: Home) -> Client:
    client = Client()
    client.force_login(h.anne)
    return client


def test_off_means_no_path() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    r = _open(_admin(h), h.persons["Bob"])
    assert (r.status_code, r.json()["code"]) == (409, "emergency_access_off")
    assert readers(doc) == {("Bob", "addressee")}
    with as_actor(h.workspace, SYSTEM):
        assert not WorkspaceAuditEvent.objects.filter(code="emergency_access_used").exists()
    assert not AccountNotice.objects.filter(code="emergency_access_used").exists()


@pytest.mark.parametrize("length", [9, 201])
def test_a_reason_outside_10_to_200_characters_is_422(length: int) -> None:
    h = home()
    _on(h)
    assert _open(_admin(h), h.persons["Bob"], "x" * length).status_code == 422


def test_on_opens_the_persons_private_letters_for_one_hour() -> None:
    h = home()
    _on(h)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client = _admin(h)
    assert _get(client, doc) == 404
    r = _open(client, h.persons["Bob"])
    assert r.status_code == 200, r.json()
    assert r.json()["opened_count"] == 1
    with as_actor(h.workspace, SYSTEM):
        row = DocumentReader.objects.get(document=doc, user=h.anne, reason="emergency")
    assert row.expires_at is not None
    assert abs((row.expires_at - timezone.now()) - timedelta(hours=1)) < timedelta(minutes=1)
    assert _get(client, doc) == 200


def test_each_opened_letter_shows_it_in_its_history_to_readers() -> None:
    h = home()
    _on(h)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    _open(_admin(h), h.persons["Bob"])
    client = Client()
    client.force_login(h.bob)
    events = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/{doc.id}/events").json()
    (line,) = [e for e in events if e["kind"] == "emergency_opened"]
    assert (line["note"], line["actor_name"]) == (REASON, "Anne")


def test_the_person_concerned_gets_a_notice() -> None:
    h = home()
    _on(h)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    _open(_admin(h), h.persons["Bob"])
    (notice,) = AccountNotice.objects.filter(user=h.bob, code="emergency_access_used")
    assert notice.values == {"person": "Bob", "count": "1"}


def test_use_is_audited_with_reason_and_count() -> None:
    h = home()
    _on(h)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    _open(_admin(h), h.persons["Bob"])
    with as_actor(h.workspace, SYSTEM):
        (row,) = WorkspaceAuditEvent.objects.filter(code="emergency_access_used")
    assert row.values == {"reason": REASON, "document_count": 1}
    assert (row.target_kind, row.target_id) == ("person", h.persons["Bob"].id)


def test_department_post_opens_too() -> None:
    h = home()
    _on(h)
    with as_actor(h.workspace, SYSTEM):
        group = Person.objects.create(
            workspace=h.workspace, display_name="Ouders", kind=Person.Kind.DEPARTMENT
        )
        DepartmentMember.objects.create(workspace=h.workspace, department=group, user=h.carla)
    doc = private_letter(h.workspace, addressees=[group])
    client = _admin(h)
    assert _open(client, group).json()["opened_count"] == 1
    assert _get(client, doc) == 200
    assert AccountNotice.objects.filter(user=h.carla, code="emergency_access_used").exists()


def test_expired_access_hides_the_letter_again() -> None:
    h = home()
    _on(h)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client = _admin(h)
    _open(client, h.persons["Bob"])
    with as_actor(h.workspace, SYSTEM):
        DocumentReader.objects.filter(reason="emergency").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    assert _get(client, doc) == 404


def _read_only(r: "Response") -> None:
    assert (r.status_code, r.json()["code"]) == (403, "emergency_read_only"), r.json()


def test_emergency_reader_cannot_organise_share_publish_or_retry() -> None:
    h = home()
    _on(h)
    done = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    in_review = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    failed = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=in_review.id).update(status="needs_review")
        Document.objects.filter(id=failed.id).update(status="failed")
        label = Label.objects.filter(workspace=h.workspace).first()
        assert label is not None
        suggestion = DocumentSuggestion.objects.create(
            workspace=h.workspace,
            document=done,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=label,
        )
        event = DocumentEvent.objects.create(
            workspace=h.workspace,
            document=done,
            kind=DocumentEvent.Kind.LABEL_ADDED,
            origin=DocumentEvent.Origin.RULE,
            label=label,
        )
        task = Task.objects.get(document=done)
    client = _admin(h)
    assert _open(client, h.persons["Bob"]).json()["opened_count"] == 3
    base = f"/api/v1/workspaces/{h.workspace.id}/documents"
    url = f"{base}/{done.id}"
    _read_only(client.put(f"{url}/labels", {"label_ids": []}, content_type=JSON))
    _read_only(
        client.put(
            f"{url}/addressees", {"person_ids": [str(h.persons["Anne"].id)]}, content_type=JSON
        )
    )
    _read_only(client.post(f"{url}/suggestions/{suggestion.id}/accept", content_type=JSON))
    _read_only(client.post(f"{url}/suggestions/{suggestion.id}/dismiss", content_type=JSON))
    _read_only(client.post(f"{url}/events/{event.id}/undo", content_type=JSON))
    _read_only(
        client.put(
            f"{url}/visibility",
            {"visibility": "workspace", "shared_user_ids": []},
            content_type=JSON,
        )
    )
    _read_only(client.post(f"{url}/trash", content_type=JSON))
    _read_only(
        client.post(
            f"{base}/{in_review.id}/confirm",
            {"sender": "", "document_type": "", "summary": "", "summary_by_ai": False, "tasks": []},
            content_type=JSON,
        )
    )
    tasks = f"/api/v1/workspaces/{h.workspace.id}/tasks/{task.id}"
    _read_only(client.post(f"{tasks}/complete", content_type=JSON))
    _read_only(client.post(f"{tasks}/assign", {"user_id": str(h.anne.id)}, content_type=JSON))
    _read_only(client.post(f"{base}/{failed.id}/retry", content_type=JSON))
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=failed.id).status == "failed"
        assert Document.objects.get(id=done.id).visibility == "restricted"
        assert Document.objects.get(id=in_review.id).status == "needs_review"
    assert readers(done) >= {("Bob", "addressee"), ("Anne", "emergency")}


def test_emergency_rows_survive_recompute_and_go_with_make_public() -> None:
    h = home()
    _on(h)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    _open(_admin(h), h.persons["Bob"])
    with as_actor(h.workspace, SYSTEM):
        visibility.recompute(doc)
    assert ("Anne", "emergency") in readers(doc)
    with as_actor(h.workspace, h.bob.id):
        visibility.make_public(doc, actor=AuditActor.for_user(h.bob), user_id=h.bob.id)
    assert readers(doc) == set()


def test_reader_names_do_not_list_the_emergency_reader() -> None:
    h = home()
    _on(h)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client = _admin(h)
    _open(client, h.persons["Bob"])
    body = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/{doc.id}").json()
    assert body["reader_names"] == ["Bob"]
    assert body["can_make_public"] is False


# Non-private post in an organisation (S 4.7: the admin does not read it either).


def test_emergency_opens_non_private_post_the_admin_cannot_see() -> None:
    o = office()
    assert "finance_letter" not in o.visible(o.admin.id)
    client = Client()
    client.force_login(o.admin)
    r = _open(client, o.persons["Finance"])
    assert r.json()["opened_count"] == 1
    assert "finance_letter" in o.visible(o.admin.id)
    assert AccountNotice.objects.filter(user=o.finance, code="emergency_access_used").exists()


def test_emergency_skips_letters_the_admin_already_sees() -> None:
    o = office()
    o.state(e=False, d=True, a=True)  # the admin reads all post that is not private
    client = Client()
    client.force_login(o.admin)
    r = _open(client, o.persons["Finance"])
    assert r.json()["opened_count"] == 0
    assert not AccountNotice.objects.filter(code="emergency_access_used").exists()


def test_emergency_reader_cannot_assign_or_share_a_non_private_letter() -> None:
    o = office()
    doc = o.letters["finance_letter"]
    client = Client()
    client.force_login(o.admin)
    _open(client, o.persons["Finance"])
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.get(document=doc)
    _read_only(
        client.put(
            f"/api/v1/workspaces/{o.workspace.id}/documents/{doc.id}/visibility",
            {"visibility": "workspace", "shared_user_ids": [str(o.plain.id)]},
            content_type=JSON,
        )
    )
    _read_only(
        client.post(
            f"/api/v1/workspaces/{o.workspace.id}/tasks/{task.id}/assign",
            {"user_id": str(o.finance.id)},
            content_type=JSON,
        )
    )
