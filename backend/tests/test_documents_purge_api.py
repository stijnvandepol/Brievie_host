"""purgeDocument and emptyTrash: admin, web session only, from the trash the admin sees.

These endpoints only mark letters and queue the purge job; the job is not run here, so
no file is removed by these tests. The autouse fixture still proves the storage root is
this test's tmp_path before every test (conftest sets it).
"""

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.backup import safepaths
from apps.documents import purge, retention
from apps.documents.models import Document
from apps.documents.retention import RemovalVerdict
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for, make_document, trashed
from tests.helpers import post_json
from tests.members_support import as_user, join, workspace_rows
from tests.visibility_support import office


@pytest.fixture(autouse=True)
def _storage_in_tmp(tmp_path: Path) -> None:
    assert Path(settings.BRIEVIE_STORAGE_ROOT) == tmp_path / "documents"
    assert safepaths.installed_test_root() == tmp_path.resolve()


def purge_url(ws: object, document_id: object) -> str:
    return f"/api/v1/workspaces/{ws}/trash/{document_id}"


def empty_path(ws: object) -> str:
    return f"/workspaces/{ws}/trash/empty"


def list_url(ws: object) -> str:
    return f"/api/v1/workspaces/{ws}/trash"


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def purge_jobs(ws: Workspace) -> int:
    return ProcrastinateJob.objects.filter(
        task_name="purge_workspace_trash", args__workspace_id=str(ws.id)
    ).count()


@pytest.fixture
def admin_a(ws_a: Workspace) -> Client:
    return as_user(admin_of(ws_a))


def test_admin_purges_now(admin_a: Client, ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a))
    stays = trashed(make_document(ws_a))

    r = admin_a.delete(purge_url(ws_a.id, document.id))

    assert r.status_code == 204, r.content
    items = admin_a.get(list_url(ws_a.id)).json()["items"]
    assert [i["id"] for i in items] == [str(stays.id)]
    assert fresh(document).purge_started_at is not None
    assert fresh(stays).purge_started_at is None
    assert purge_jobs(ws_a) == 1
    [row] = workspace_rows(ws_a, "document_purged")
    assert (row.actor_kind, row.actor_id, row.target_id) == ("user", admin_of(ws_a).id, document.id)
    assert row.values == {"reason": "admin"}
    # A second time: the purge started, so it is not in the trash any more.
    again = admin_a.delete(purge_url(ws_a.id, document.id))
    assert again.status_code == 404
    assert again.json()["code"] == "document_not_found"


def test_purge_now_needs_trash_first(admin_a: Client, ws_a: Workspace) -> None:
    live = make_document(ws_a)

    for document_id in (live.id, uuid.uuid4()):
        r = admin_a.delete(purge_url(ws_a.id, document_id))
        assert r.status_code == 404
        assert r.json()["code"] == "document_not_found"
    assert fresh(live).trashed_at is None
    assert purge_jobs(ws_a) == 0


def test_member_cannot_purge_or_empty(ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a))
    for role in (Membership.Role.MEMBER, Membership.Role.VIEWER):
        caller = as_user(join(ws_a, role))
        r = caller.delete(purge_url(ws_a.id, document.id))
        assert r.status_code == 403, role
        assert r.json()["code"] == "forbidden"
        r = post_json(caller, empty_path(ws_a.id), {})
        assert r.status_code == 403, role
        assert r.json()["code"] == "forbidden"
    assert fresh(document).purge_started_at is None
    assert purge_jobs(ws_a) == 0


def test_purge_now_of_unseen_letter_is_404() -> None:
    o = office()
    ws = o.workspace
    private = o.letters["private_femke"]
    o.state(e=False, d=True, a=True)  # admins read all post, a private letter still not
    seen_by_admin = sorted(o.visible(o.admin.id) - {"private_femke"})
    assert seen_by_admin
    assert "private_femke" not in o.visible(o.admin.id)
    seen = o.letters[seen_by_admin[0]]
    finance = as_user(o.finance)
    assert (
        post_json(finance, f"/workspaces/{ws.id}/documents/{private.id}/trash").status_code == 200
    )
    trashed(seen)
    admin = as_user(o.admin)
    missing = admin.delete(purge_url(ws.id, uuid.uuid4())).json()

    r = admin.delete(purge_url(ws.id, private.id))
    assert r.status_code == 404
    assert r.json() == missing
    emptied = post_json(admin, empty_path(ws.id), {})
    assert emptied.status_code == 200
    assert emptied.json() == {"document_count": 1}

    assert fresh(private).trashed_at is not None
    assert fresh(private).purge_started_at is None
    assert fresh(seen).purge_started_at is not None
    assert [row.target_id for row in workspace_rows(ws, "document_purged")] == [seen.id]
    assert [i["id"] for i in finance.get(list_url(ws.id)).json()["items"]] == [str(private.id)]


def test_device_token_cannot_purge_or_empty(client: Client, ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a))
    headers = bearer_for(admin_of(ws_a))

    r = client.delete(purge_url(ws_a.id, document.id), headers=headers)
    assert r.status_code == 401
    r = post_json(client, empty_path(ws_a.id), {}, headers=headers)
    assert r.status_code == 401
    assert fresh(document).purge_started_at is None
    assert purge_jobs(ws_a) == 0


def test_empty_trash_respects_up_to(admin_a: Client, ws_a: Workspace) -> None:
    older = trashed(make_document(ws_a), days_ago=5)
    middle = trashed(make_document(ws_a), days_ago=3)
    newer = trashed(make_document(ws_a), days_ago=1)
    up_to = middle.trashed_at
    assert up_to is not None

    r = post_json(admin_a, empty_path(ws_a.id), {"up_to": up_to.isoformat()})

    assert r.status_code == 200, r.content
    assert r.json() == {"document_count": 2}
    items = admin_a.get(list_url(ws_a.id)).json()["items"]
    assert [i["id"] for i in items] == [str(newer.id)]
    rows = workspace_rows(ws_a, "document_purged")
    assert sorted(str(row.target_id) for row in rows) == sorted([str(older.id), str(middle.id)])
    assert all(row.values == {"reason": "emptied"} for row in rows)
    assert all(row.actor_id == admin_of(ws_a).id for row in rows)
    assert purge_jobs(ws_a) == 1


def test_empty_trash_without_up_to_takes_all(admin_a: Client, ws_a: Workspace) -> None:
    letters = [trashed(make_document(ws_a), days_ago=n) for n in (0, 2, 9)]
    live = make_document(ws_a)

    r = post_json(admin_a, empty_path(ws_a.id))

    assert r.status_code == 200, r.content
    assert r.json() == {"document_count": 3}
    assert admin_a.get(list_url(ws_a.id)).json()["items"] == []
    assert all(fresh(d).purge_started_at is not None for d in letters)
    assert fresh(live).trashed_at is None
    assert purge_jobs(ws_a) == 1


def test_empty_of_an_empty_trash_queues_nothing(admin_a: Client, ws_a: Workspace) -> None:
    r = post_json(admin_a, empty_path(ws_a.id), {"up_to": None})

    assert r.status_code == 200
    assert r.json() == {"document_count": 0}
    assert purge_jobs(ws_a) == 0


def test_job_deferred_twice_is_fine(admin_a: Client, ws_a: Workspace) -> None:
    first = trashed(make_document(ws_a))
    second = trashed(make_document(ws_a))

    assert admin_a.delete(purge_url(ws_a.id, first.id)).status_code == 204
    assert admin_a.delete(purge_url(ws_a.id, second.id)).status_code == 204
    assert post_json(admin_a, empty_path(ws_a.id), {}).status_code == 200

    assert purge_jobs(ws_a) == 1
    assert fresh(first).purge_started_at is not None
    assert fresh(second).purge_started_at is not None


def test_retention_hook_guards_every_purge_path(
    admin_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = trashed(make_document(ws_a), days_ago=40)
    goes = trashed(make_document(ws_a), days_ago=40)

    def check(document: Document, *, step: retention.RemovalStep) -> RemovalVerdict:
        assert step == "purge"
        if document.id == kept.id:
            return RemovalVerdict(allowed=False, warning="Bewaar deze brief tot 2030.")
        return RemovalVerdict()

    monkeypatch.setattr(retention, "check_removal", check)

    r = admin_a.delete(purge_url(ws_a.id, kept.id))
    assert r.status_code == 409
    assert r.json() == {"code": "document_retained", "message": "Bewaar deze brief tot 2030."}
    assert fresh(kept).purge_started_at is None

    assert purge.mark_expired(ws_a.id, now=timezone.now()) == 1
    assert fresh(kept).purge_started_at is None
    assert fresh(goes).purge_started_at is not None

    other = trashed(make_document(ws_a), days_ago=1)
    r = post_json(admin_a, empty_path(ws_a.id), {})
    assert r.json() == {"document_count": 1}
    assert fresh(other).purge_started_at is not None
    assert fresh(kept).purge_started_at is None
    assert [i["id"] for i in admin_a.get(list_url(ws_a.id)).json()["items"]] == [str(kept.id)]
    assert {row.target_id for row in workspace_rows(ws_a, "document_purged")} == {
        goes.id,
        other.id,
    }


def test_purge_now_answer_carries_no_body(admin_a: Client, ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a))

    r = admin_a.delete(purge_url(ws_a.id, document.id))

    assert r.status_code == 204
    assert r.content == b""


def test_empty_trash_rejects_a_bad_up_to(admin_a: Client, ws_a: Workspace) -> None:
    trashed(make_document(ws_a))

    r = post_json(admin_a, empty_path(ws_a.id), {"up_to": "gisteren"})

    assert r.status_code == 422
    assert workspace_rows(ws_a, "document_purged") == []
