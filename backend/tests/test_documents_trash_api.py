"""The trash over the API: trashDocument, restoreDocument and listTrash, with the rights of
the roles and of the policies (a letter the caller may not see answers as a missing one)."""

import uuid
from datetime import datetime, timedelta

import pytest
from django.test import Client

from apps.documents import retention, trash
from apps.documents.models import Document
from apps.documents.retention import RemovalVerdict
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for, documents_url, make_document, trashed
from tests.helpers import post_json
from tests.members_support import as_user, join, workspace_rows
from tests.visibility_support import office


def trash_path(ws: object, document_id: object) -> str:
    return f"/workspaces/{ws}/documents/{document_id}/trash"


def restore_path(ws: object, document_id: object) -> str:
    return f"/workspaces/{ws}/documents/{document_id}/restore"


def list_url(ws: object) -> str:
    return f"/api/v1/workspaces/{ws}/trash"


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_member_trashes_with_device_token(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a)
    document = make_document(ws_a, "done")

    r = post_json(client, trash_path(ws_a.id, document.id), headers=bearer_for(member))

    assert r.status_code == 200, r.content
    out = r.json()
    assert out["id"] == str(document.id)
    assert when(out["purge_after"]) == when(out["trashed_at"]) + timedelta(days=30)
    assert out["trashed_by_name"] == member.name
    assert fresh(document).trashed_by_id == member.id
    [row] = workspace_rows(ws_a, "document_trashed")
    assert (row.actor_kind, row.actor_id) == ("device", member.id)


def test_viewer_cannot_trash_or_restore(ws_a: Workspace) -> None:
    viewer = as_user(join(ws_a, Membership.Role.VIEWER))
    live = make_document(ws_a, "done")
    gone = trashed(make_document(ws_a, "done"))

    for path in (trash_path(ws_a.id, live.id), restore_path(ws_a.id, gone.id)):
        r = post_json(viewer, path)
        assert r.status_code == 403, path
        assert r.json()["code"] == "forbidden"
    assert fresh(live).trashed_at is None
    assert fresh(gone).trashed_at is not None


def test_viewer_sees_trash_list(client_a: Client, ws_a: Workspace) -> None:
    viewer = as_user(join(ws_a, Membership.Role.VIEWER))
    document = make_document(ws_a, "done")
    make_document(ws_a, "done")
    assert post_json(client_a, trash_path(ws_a.id, document.id)).status_code == 200

    r = viewer.get(list_url(ws_a.id))

    assert r.status_code == 200
    out = r.json()
    assert [item["id"] for item in out["items"]] == [str(document.id)]
    assert out["retention_days"] == 30
    assert out["items"][0]["trashed_by_name"] == admin_of(ws_a).name


def test_trash_of_other_workspace_letter_404(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    other = make_document(ws_b, "done")
    gone = trashed(make_document(ws_b, "done"))

    for path in (trash_path(ws_a.id, other.id), restore_path(ws_a.id, gone.id)):
        r = post_json(client_a, path)
        assert r.status_code == 404, path
        assert r.json()["code"] == "document_not_found"
    assert fresh(other).trashed_at is None
    assert fresh(gone).trashed_at is not None


def test_restore_not_in_trash_404(client_a: Client, ws_a: Workspace) -> None:
    live = make_document(ws_a, "done")

    missing = post_json(client_a, restore_path(ws_a.id, uuid.uuid4()))
    unchanged = post_json(client_a, restore_path(ws_a.id, live.id))

    assert missing.status_code == 404
    assert missing.json()["code"] == "document_not_found"
    assert unchanged.status_code == 200
    assert unchanged.json()["id"] == str(live.id)
    assert workspace_rows(ws_a, "document_restored") == []


def test_restore_answers_the_letter(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    post_json(client_a, trash_path(ws_a.id, document.id))

    r = post_json(client_a, restore_path(ws_a.id, document.id))

    assert r.status_code == 200
    assert r.json()["id"] == str(document.id)
    assert r.json()["can_read"] is True
    assert client_a.get(f"{documents_url(ws_a.id)}/{document.id}").status_code == 200


def test_trash_follows_visibility(client: Client) -> None:
    o = office()
    private = o.letters["private_femke"]
    finance = as_user(o.finance)
    r = post_json(finance, trash_path(o.workspace.id, private.id))
    assert r.status_code == 200, r.content
    missing = post_json(finance, restore_path(o.workspace.id, uuid.uuid4())).json()

    for user in (o.plain, o.hr, o.admin):
        caller = as_user(user)
        listed = caller.get(list_url(o.workspace.id)).json()
        items = listed["items"]
        assert str(private.id) not in {item["id"] for item in items}, user.name
        assert items == [], user.name
        assert listed["total"] == 0, user.name
        for path in (
            restore_path(o.workspace.id, private.id),
            trash_path(o.workspace.id, private.id),
        ):
            answer = post_json(caller, path)
            assert answer.status_code == 404, (user.name, path)
            assert answer.json() == missing, (user.name, path)
        detail = caller.get(f"{documents_url(o.workspace.id)}/{private.id}")
        assert detail.json()["code"] == "document_not_found", user.name
    assert fresh(private).trashed_at is not None

    assert finance.get(f"{documents_url(o.workspace.id)}/{private.id}").json()["code"] == (
        "document_in_trash"
    )
    back = post_json(finance, restore_path(o.workspace.id, private.id))
    assert back.status_code == 200
    assert fresh(private).trashed_at is None


def test_trash_list_newest_first_and_capped(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = trashed(make_document(ws_a, "done"), days_ago=3)
    newest = trashed(make_document(ws_a, "done"), days_ago=1)
    middle = trashed(make_document(ws_a, "done"), days_ago=2)
    trashed(make_document(ws_a, "done"), purge_started=True)

    all_items = client_a.get(list_url(ws_a.id)).json()["items"]
    monkeypatch.setattr(trash, "TRASH_LIST_LIMIT", 2)
    capped_out = client_a.get(list_url(ws_a.id)).json()
    capped = capped_out["items"]

    assert [i["id"] for i in all_items] == [str(d.id) for d in (newest, middle, older)]
    assert [i["id"] for i in capped] == [str(newest.id), str(middle.id)]
    # "Prullenbak legen" counts every letter it empties, beyond the cap too.
    assert capped_out["total"] == 3
    assert all_items[0]["trashed_by_name"] == ""


def test_retention_hook_allows_today(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    assert retention.check_removal(document, step="trash") == RemovalVerdict()
    assert retention.check_removal(document, step="purge") == RemovalVerdict()


def test_trash_refused_by_retention_hook(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = make_document(ws_a, "done")
    monkeypatch.setattr(
        retention,
        "check_removal",
        lambda document, *, step: RemovalVerdict(allowed=False, warning="x"),
    )

    r = post_json(client_a, trash_path(ws_a.id, document.id))

    assert r.status_code == 409
    assert r.json()["code"] == "document_retained"
    assert fresh(document).trashed_at is None
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not document.events.exists()
    assert workspace_rows(ws_a, "document_trashed") == []
