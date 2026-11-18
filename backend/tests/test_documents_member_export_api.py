"""A member exports their own post: who may, what is in it, who may download it, how it
is stored and when it is removed. Everything through the HTTP endpoints as real roles."""

import io
import logging
import os
import stat
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.codes import AuditCode
from apps.documents import export, member_export
from apps.documents.export_files import (
    UnsafePathError,
    export_root,
    judge_export_path,
    remove_export_file,
)
from apps.documents.jobs import build_member_export, remove_expired_exports
from apps.documents.models import MemberExport
from apps.people.services import ensure_person_for_member
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.export_support import export_documents, read_zip, sealed_document
from tests.helpers import post_json
from tests.members_support import admin_of, as_user, bearer, join, paired, workspace_rows

if TYPE_CHECKING:
    from tests.helpers import Response

    type Commits = Callable[..., AbstractContextManager[list[Callable[[], object]]]]


def url(ws: Workspace, tail: str = "") -> str:
    return f"/api/v1/workspaces/{ws.id}/my-export{tail}"


def run_member_export(workspace_id: UUID, export_id: UUID) -> None:
    build_member_export(workspace_id=str(workspace_id), export_id=str(export_id))


def person_of(ws: Workspace, user: User) -> object:
    with workspace_context(ws.id):
        return ensure_person_for_member(workspace=ws, user=user)


def rows(ws: Workspace) -> list[MemberExport]:
    with workspace_context(ws.id):
        return list(MemberExport.objects.order_by("created_at"))


def export_of(ws: Workspace, user: User) -> MemberExport:
    with workspace_context(ws.id):
        return MemberExport.objects.get(user=user)


def file_of(row: MemberExport) -> Path:
    return Path(settings.BRIEVIE_STORAGE_ROOT) / row.storage_path


def build(ws: Workspace, user: User, commits: "Commits | None" = None) -> MemberExport:
    """Ask for an export as the user and let the job run. `commits` runs the on-commit
    callbacks (the test's own transaction never commits) of the request."""
    if commits is None:
        r = post_json(as_user(user), f"/workspaces/{ws.id}/my-export")
    else:
        with commits(execute=True):
            r = post_json(as_user(user), f"/workspaces/{ws.id}/my-export")
    assert r.status_code == 202, r.content
    row = export_of(ws, user)
    run_member_export(ws.id, row.id)
    return export_of(ws, user)


def member_with_post(ws: Workspace, role: str = Membership.Role.MEMBER) -> tuple[User, list[UUID]]:
    """A member with a person of their own and two letters on their name, plus one on
    somebody else's name."""
    user = join(ws, role)
    mine = person_of(ws, user)
    other = person_of(ws, join(ws))
    first, _p = sealed_document(ws, admin_of(ws), pages=2, persons=[mine], sender="Mijn brief 1")  # type: ignore[list-item]
    second, _p = sealed_document(ws, admin_of(ws), pages=1, persons=[mine], sender="Mijn brief 2")  # type: ignore[list-item]
    sealed_document(ws, admin_of(ws), pages=1, persons=[other], sender="Van een ander")  # type: ignore[list-item]
    sealed_document(ws, admin_of(ws), pages=1, sender="Van niemand")
    return user, [first.id, second.id]


def get_zip(client: Client, ws: Workspace) -> "tuple[Response, bytes]":
    r = client.get(url(ws, "/file"))
    assert r.status_code == 200, r.content
    return r, b"".join(r.streaming_content)  # type: ignore[attr-defined]


def test_member_document_ids_are_the_documents_on_their_name(ws_a: Workspace) -> None:
    user, mine = member_with_post(ws_a)

    assert set(member_export.member_document_ids(ws_a.id, user.id)) == set(mine)
    nobody = join(ws_a)
    assert member_export.member_document_ids(ws_a.id, nobody.id) == []
    # The same member in another workspace has nothing there.
    assert member_export.member_document_ids(uuid4(), user.id) == []


def test_member_exports_and_downloads_their_post(ws_a: Workspace) -> None:
    user, mine = member_with_post(ws_a)
    client = as_user(user)

    r = post_json(client, f"/workspaces/{ws_a.id}/my-export")
    assert r.status_code == 202
    assert r.json()["status"] == "pending"
    assert r.json()["allowed"] is True
    pending = client.get(url(ws_a))
    assert pending.json()["status"] == "pending"

    run_member_export(ws_a.id, export_of(ws_a, user).id)

    state = client.get(url(ws_a)).json()
    assert state["status"] == "ready"
    assert state["document_count"] == 2
    assert state["size"] > 0
    assert state["expires_at"] is not None
    response, payload = get_zip(client, ws_a)
    assert response["Content-Type"] == "application/zip"
    assert response["Cache-Control"] == "private, no-store"
    today = timezone.localdate().isoformat()
    expected = f'attachment; filename="brievie-mijn-post-{today}.zip"'
    assert response["Content-Disposition"] == expected
    archive = read_zip(payload)
    assert archive.testzip() is None
    assert {e["id"] for e in export_documents(archive)} == {str(i) for i in mine}
    senders = sorted(str(e["sender"]) for e in export_documents(archive))
    assert senders == ["Mijn brief 1", "Mijn brief 2"]
    assert b"Van een ander" not in payload
    assert not any("Van niemand" in n for n in archive.namelist())
    assert len([n for n in archive.namelist() if n.endswith(".jpg")]) == 3


def test_a_viewer_exports_their_own_post_too(ws_a: Workspace) -> None:
    user, mine = member_with_post(ws_a, Membership.Role.VIEWER)

    build(ws_a, user)

    _, payload = get_zip(as_user(user), ws_a)
    assert {e["id"] for e in export_documents(read_zip(payload))} == {str(i) for i in mine}


def test_a_missing_export_answers_404_before_streaming(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    client = as_user(user)

    nothing = client.get(url(ws_a, "/file"))
    assert (nothing.status_code, nothing.streaming) == (404, False)
    assert nothing.json()["code"] == "export_not_found"

    row = build(ws_a, user)
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=row.id).update(expires_at=timezone.now())
    expired = client.get(url(ws_a, "/file"))
    assert (expired.status_code, expired.streaming) == (404, False)
    assert expired.json()["code"] == "export_not_found"
    assert expired["Content-Type"].startswith("application/json")

    # A pending or failed export is not a file either.
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=row.id).update(
            expires_at=timezone.now() + timedelta(hours=1), status="pending"
        )
    assert client.get(url(ws_a, "/file")).status_code == 404
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=row.id).update(status="failed")
    assert client.get(url(ws_a, "/file")).status_code == 404


def test_a_ready_export_whose_file_is_gone_answers_404(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    row = build(ws_a, user)
    file_of(row).unlink()

    r = as_user(user).get(url(ws_a, "/file"))

    assert (r.status_code, r.streaming) == (404, False)
    assert r.json()["code"] == "export_not_found"


def test_the_download_streams_after_the_request_transaction(ws_a: Workspace) -> None:
    user, mine = member_with_post(ws_a)
    build(ws_a, user)
    response = as_user(user).get(url(ws_a, "/file"))
    assert response.streaming

    # The view has returned: its transaction and RLS context are over. The stream needs
    # neither, so it reads no row at all.
    with CaptureQueriesContext(connection) as queries:
        payload = b"".join(response.streaming_content)  # type: ignore[attr-defined]

    assert len(queries) == 0
    assert {e["id"] for e in export_documents(read_zip(payload))} == {str(i) for i in mine}


def test_the_file_on_disk_is_sealed_and_stored_under_an_unguessable_name(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    row = build(ws_a, user)
    _, payload = get_zip(as_user(user), ws_a)
    archive = read_zip(payload)
    first_page = archive.read(next(n for n in archive.namelist() if n.endswith(".jpg")))

    path = file_of(row)
    stored = path.read_bytes()
    assert path.is_relative_to(Path(settings.BRIEVIE_STORAGE_ROOT) / "exports")
    assert row.storage_path == f"exports/{ws_a.id}/{row.id}.sealed"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert b"PK\x03\x04" not in stored
    assert first_page[:200] not in stored
    assert b"Mijn brief" not in stored
    assert row.size == len(stored) or row.size > 0
    # Nothing unsealed is left next to it.
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_another_member_an_admin_and_a_stranger_cannot_download_it(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    user, _mine = member_with_post(ws_a)
    build(ws_a, user)
    other_member = join(ws_a)
    other_viewer = join(ws_a, Membership.Role.VIEWER)

    for who in (other_member, other_viewer, admin_of(ws_a)):
        r = as_user(who).get(url(ws_a, "/file"))
        assert (r.status_code, r.streaming) == (404, False)
        assert r.json()["code"] == "export_not_found"
        assert as_user(who).get(url(ws_a)).json()["status"] == "none"
    stranger = as_user(admin_of(ws_b)).get(url(ws_a, "/file"))
    assert stranger.status_code == 404
    assert stranger.json()["code"] == "workspace_not_found"
    assert as_user(admin_of(ws_b)).get(url(ws_a)).status_code == 404
    assert post_json(as_user(admin_of(ws_b)), f"/workspaces/{ws_a.id}/my-export").status_code == 404
    assert Client().get(url(ws_a, "/file")).status_code == 401
    # Nothing of this was logged as a download.
    assert workspace_rows(ws_a, AuditCode.EXPORT_DOWNLOADED) == []


def test_a_device_token_gets_401_on_every_export_endpoint(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    build(ws_a, user)
    _, token = paired(user)
    client = Client()

    assert client.get(url(ws_a), headers=bearer(token)).status_code == 401
    assert client.get(url(ws_a, "/file"), headers=bearer(token)).status_code == 401
    assert (
        client.post(url(ws_a), content_type="application/json", headers=bearer(token)).status_code
        == 401
    )


def test_business_workspace_refuses_until_allowed(ws_b: Workspace) -> None:
    user, _mine = member_with_post(ws_b)
    client = as_user(user)

    assert client.get(url(ws_b)).json() == {
        "allowed": False,
        "status": "none",
        "id": None,
        "created_at": None,
        "expires_at": None,
        "document_count": 0,
        "size": 0,
    }
    refused = post_json(client, f"/workspaces/{ws_b.id}/my-export")
    assert refused.status_code == 403
    assert refused.json() == {
        "code": "export_not_allowed",
        "message": "In deze werkruimte staat exporteren uit. Vraag de beheerder.",
    }
    assert rows(ws_b) == []

    allowed = as_user(admin_of(ws_b)).put(
        f"/api/v1/workspaces/{ws_b.id}/settings",
        {"allow_member_export": True},
        content_type="application/json",
    )
    assert allowed.status_code == 200
    assert post_json(client, f"/workspaces/{ws_b.id}/my-export").status_code == 202


def test_switching_exports_off_stops_the_download_of_an_export_already_made(
    ws_a: Workspace,
) -> None:
    user, _mine = member_with_post(ws_a)
    build(ws_a, user)
    off = as_user(admin_of(ws_a)).put(
        f"/api/v1/workspaces/{ws_a.id}/settings",
        {"allow_member_export": False},
        content_type="application/json",
    )
    assert off.status_code == 200

    r = as_user(user).get(url(ws_a, "/file"))

    assert (r.status_code, r.streaming) == (403, False)
    assert r.json()["code"] == "export_not_allowed"


def test_a_second_request_while_one_runs_is_refused(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    client = as_user(user)
    assert post_json(client, f"/workspaces/{ws_a.id}/my-export").status_code == 202

    again = post_json(client, f"/workspaces/{ws_a.id}/my-export")

    assert again.status_code == 409
    assert again.json()["code"] == "export_running"
    assert len(rows(ws_a)) == 1


def test_a_new_request_replaces_the_older_export_and_removes_its_file(
    ws_a: Workspace, django_capture_on_commit_callbacks: "Commits"
) -> None:
    user, _mine = member_with_post(ws_a)
    old = build(ws_a, user)
    old_path = file_of(old)
    assert old_path.exists()

    new = build(ws_a, user, django_capture_on_commit_callbacks)

    assert new.id != old.id
    assert not old_path.exists()
    assert file_of(new).exists()
    assert [r.id for r in rows(ws_a)] == [new.id]


def test_expired_export_is_removed(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    kept_user, _other = member_with_post(ws_a)
    row = build(ws_a, user)
    kept = build(ws_a, kept_user)
    assert row.expires_at is not None
    assert timedelta(hours=23) < row.expires_at - timezone.now() <= timedelta(hours=24)
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=row.id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

    remove_expired_exports(timestamp=0)

    assert not file_of(row).exists()
    assert [r.id for r in rows(ws_a)] == [kept.id]
    assert file_of(kept).exists()
    assert as_user(user).get(url(ws_a)).json()["status"] == "none"


def test_a_stuck_pending_export_loses_its_partial_file(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    recent_user, _other = member_with_post(ws_a)
    for who in (user, recent_user):
        assert post_json(as_user(who), f"/workspaces/{ws_a.id}/my-export").status_code == 202
    stuck, recent = export_of(ws_a, user), export_of(ws_a, recent_user)
    for row in (stuck, recent):
        file_of(row).parent.mkdir(parents=True, exist_ok=True)
        file_of(row).write_bytes(b"partial")
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=stuck.id).update(
            created_at=timezone.now() - timedelta(hours=3)
        )

    remove_expired_exports(timestamp=0)

    assert export_of(ws_a, user).status == "failed"
    assert not file_of(stuck).exists()
    # A recent one may still be running: left alone.
    assert export_of(ws_a, recent_user).status == "pending"
    assert file_of(recent).exists()
    assert as_user(user).get(url(ws_a)).json()["status"] == "failed"


def test_a_failed_export_can_be_asked_for_again(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    post_json(as_user(user), f"/workspaces/{ws_a.id}/my-export")
    with workspace_context(ws_a.id):
        MemberExport.objects.update(status="failed")

    assert post_json(as_user(user), f"/workspaces/{ws_a.id}/my-export").status_code == 202
    assert len(rows(ws_a)) == 1


def test_a_failing_job_marks_it_failed_removes_the_partial_file_and_logs_the_type_only(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user, _mine = member_with_post(ws_a)
    post_json(as_user(user), f"/workspaces/{ws_a.id}/my-export")
    row = export_of(ws_a, user)

    def broken(**kwargs: object) -> export.ExportCounts:
        target = kwargs["target"]
        assert isinstance(target, io.RawIOBase)
        target.write(b"some bytes that are on disk by now")
        raise ValueError("Brief van Belastingdienst voor Oma, BSN 123456782")

    monkeypatch.setattr("apps.documents.member_export.write_export", broken)
    with caplog.at_level(logging.INFO):
        run_member_export(ws_a.id, row.id)

    assert export_of(ws_a, user).status == "failed"
    assert not file_of(row).exists()
    assert "ValueError" in caplog.text
    assert "Belastingdienst" not in caplog.text
    assert "123456782" not in caplog.text


def test_a_job_for_an_export_that_is_gone_or_done_writes_nothing(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    ready = build(ws_a, user)
    before = file_of(ready).read_bytes()

    run_member_export(ws_a.id, ready.id)
    run_member_export(ws_a.id, uuid4())

    assert file_of(ready).read_bytes() == before
    exports_dir = export_root()
    assert sorted(p.name for p in exports_dir.rglob("*") if p.is_file()) == [file_of(ready).name]


def test_removing_the_member_removes_their_export(
    ws_a: Workspace, django_capture_on_commit_callbacks: "Commits"
) -> None:
    leaving, _mine = member_with_post(ws_a)
    staying, _other = member_with_post(ws_a)
    gone, kept = build(ws_a, leaving), build(ws_a, staying)

    with django_capture_on_commit_callbacks(execute=True):
        r = as_user(admin_of(ws_a)).delete(f"/api/v1/workspaces/{ws_a.id}/members/{leaving.id}")

    assert r.status_code == 204, r.content
    assert not file_of(gone).exists()
    assert [row.id for row in rows(ws_a)] == [kept.id]
    assert file_of(kept).exists()


def test_leaving_removes_the_export_too(
    ws_a: Workspace, django_capture_on_commit_callbacks: "Commits"
) -> None:
    leaving, _mine = member_with_post(ws_a)
    gone = build(ws_a, leaving)

    with django_capture_on_commit_callbacks(execute=True):
        r = post_json(as_user(leaving), f"/workspaces/{ws_a.id}/leave")

    assert r.status_code == 204, r.content
    assert not file_of(gone).exists()
    assert rows(ws_a) == []


def test_export_is_logged(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    row = build(ws_a, user)
    get_zip(as_user(user), ws_a)

    (created,) = workspace_rows(ws_a, AuditCode.EXPORT_CREATED)
    (downloaded,) = workspace_rows(ws_a, AuditCode.EXPORT_DOWNLOADED)

    assert created.values == {"document_count": 2}
    assert (created.actor_kind, created.actor_id) == ("user", user.id)
    assert created.actor_name == user.name
    assert (created.target_kind, created.target_id, created.target_name) == ("export", row.id, "")
    assert (downloaded.actor_id, downloaded.target_id) == (user.id, row.id)
    assert downloaded.values == {}
    for logged in (created, downloaded):
        assert "Mijn brief" not in repr((logged.values, logged.target_name))
        assert row.storage_path not in repr(logged.values)


def test_a_refused_request_is_not_logged(ws_b: Workspace) -> None:
    user, _mine = member_with_post(ws_b)

    post_json(as_user(user), f"/workspaces/{ws_b.id}/my-export")

    assert workspace_rows(ws_b, AuditCode.EXPORT_CREATED) == []


def test_asking_and_downloading_are_rate_limited(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    client = as_user(user)
    with workspace_context(ws_a.id):
        MemberExport.objects.all().delete()

    asked = []
    for _ in range(8):
        asked.append(post_json(client, f"/workspaces/{ws_a.id}/my-export").status_code)
        with workspace_context(ws_a.id):
            MemberExport.objects.update(status="failed")
    assert 429 in asked
    assert asked.index(429) == 5

    build_row = export_of(ws_a, user)
    with workspace_context(ws_a.id):
        MemberExport.objects.filter(id=build_row.id).update(
            status="ready", expires_at=timezone.now() + timedelta(hours=1)
        )
    statuses = [client.get(url(ws_a, "/file")).status_code for _ in range(25)]
    assert 429 in statuses


def test_a_cut_off_file_fails_the_stream_instead_of_ending_it_quietly(ws_a: Workspace) -> None:
    user, _mine = member_with_post(ws_a)
    row = build(ws_a, user)
    path = file_of(row)
    path.write_bytes(path.read_bytes()[:-30])
    response = as_user(user).get(url(ws_a, "/file"))

    from apps.common.crypto import DecryptionError

    with pytest.raises(DecryptionError):
        b"".join(response.streaming_content)  # type: ignore[attr-defined]


def test_the_export_directory_is_inside_the_storage_root(settings: Settings) -> None:
    assert export_root() == Path(settings.BRIEVIE_STORAGE_ROOT) / "exports"


# --- the path guard: a pure judgement, tested without touching a real path --------------

ROOT = Path("/srv/brievie/documents/exports")
WS, EX = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
GOOD = ROOT / WS / f"{EX}.sealed"


def test_the_guard_accepts_only_an_export_file() -> None:
    assert judge_export_path(GOOD, ROOT) == GOOD


@pytest.mark.parametrize(
    "path",
    [
        Path("/"),
        Path("/etc/passwd"),
        Path.home(),
        Path.home() / "Documents" / "x.sealed",
        ROOT,
        ROOT / WS,
        ROOT / f"{EX}.sealed",
        ROOT / WS / EX,
        ROOT / WS / f"{EX}.zip",
        ROOT / WS / ".." / ".." / "documents" / f"{EX}.sealed",
        ROOT / WS / "sub" / f"{EX}.sealed",
        ROOT.parent / WS / f"{EX}.sealed",
        Path("/srv/brievie/documents/exports-evil") / WS / f"{EX}.sealed",
        Path("relative") / WS / f"{EX}.sealed",
        Path("exports") / WS / f"{EX}.sealed",
        ROOT / "not-a-uuid" / f"{EX}.sealed",
        ROOT / WS / "not-a-uuid.sealed",
        ROOT / WS / f"{EX}.sealed\0",
    ],
)
def test_the_guard_refuses_everything_else(path: Path) -> None:
    with pytest.raises(UnsafePathError):
        judge_export_path(path, ROOT)


@pytest.mark.parametrize(
    "root",
    [Path("/"), Path("/exports"), Path("/srv/exports"), Path.home(), Path("exports"), Path("/var")],
)
def test_the_guard_refuses_a_root_that_is_not_an_export_directory(root: Path) -> None:
    with pytest.raises(UnsafePathError):
        judge_export_path(root / WS / f"{EX}.sealed", root)


def test_removal_asks_the_guard_before_it_touches_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    touched: list[object] = []

    def recorder(*args: object, **kwargs: object) -> None:
        touched.append(args)
        pytest.fail("a refused path reached a destructive call")

    for target in ("os.unlink", "os.remove", "os.rmdir", "shutil.rmtree", "os.replace"):
        monkeypatch.setattr(target, recorder)
    monkeypatch.setattr(Path, "unlink", recorder)
    root = tmp_path / "documents" / "exports"

    for bad in (Path("/"), Path.home(), root, root / WS, tmp_path / f"{EX}.sealed"):
        with pytest.raises(UnsafePathError):
            remove_export_file(bad, root)
    assert touched == []


def test_removal_never_follows_a_link(tmp_path: Path) -> None:
    root = tmp_path / "documents" / "exports"
    (root / WS).mkdir(parents=True)
    victim = tmp_path / "keep-me"
    victim.write_text("precious")
    link = root / WS / f"{EX}.sealed"
    link.symlink_to(victim)

    with pytest.raises(UnsafePathError):
        remove_export_file(link, root)

    assert victim.read_text() == "precious"
    assert link.is_symlink()


def test_removal_refuses_a_file_reached_through_a_linked_directory(tmp_path: Path) -> None:
    root = tmp_path / "documents" / "exports"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{EX}.sealed").write_text("precious")
    (root / WS).symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError):
        remove_export_file(root / WS / f"{EX}.sealed", root)

    assert (outside / f"{EX}.sealed").read_text() == "precious"


def test_removal_removes_a_file_and_does_not_mind_a_missing_one(tmp_path: Path) -> None:
    root = tmp_path / "documents" / "exports"
    (root / WS).mkdir(parents=True)
    path = root / WS / f"{EX}.sealed"
    path.write_bytes(b"x")

    remove_export_file(path, root)
    remove_export_file(path, root)

    assert not path.exists()
    assert (root / WS).is_dir()


def test_a_stored_path_that_is_not_an_export_path_is_never_removed(
    ws_a: Workspace, tmp_path: Path
) -> None:
    user, _mine = member_with_post(ws_a)
    victim = tmp_path / "documents" / "victim.txt"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("precious")
    post_json(as_user(user), f"/workspaces/{ws_a.id}/my-export")
    with workspace_context(ws_a.id):
        MemberExport.objects.update(
            storage_path="../victim.txt", created_at=timezone.now() - timedelta(hours=3)
        )

    remove_expired_exports(timestamp=0)

    assert victim.read_text() == "precious"


def test_no_document_content_in_the_job_module() -> None:
    root = Path(member_export.__file__).parent
    for name in ("member_export.py", "jobs.py", "export_files.py"):
        assert "workspace_context(" not in (root / name).read_text(), name
