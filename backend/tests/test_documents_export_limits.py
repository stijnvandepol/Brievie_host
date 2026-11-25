"""An export's size is known before it is built (security review of PR #54, point 2): too
large for the server's limit or for its disk is refused with a reason, an "Alles
exporteren" runs one at a time per workspace, and a document's pages are never all in
memory at once."""

import io
from uuid import UUID

import pytest
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import export, member_export
from apps.documents.models import MemberExport
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.archive_support import membership_of
from tests.export_support import sealed_document
from tests.my_tasks_support import household
from tests.visibility_support import as_actor


def _ask(workspace: Workspace, user_id: UUID, scope: str) -> MemberExport:
    user = User.objects.get(id=user_id)
    member = membership_of(workspace, user)
    with as_actor(workspace, user.id):
        return member_export.create_my_export(
            membership=member, actor=AuditActor.for_user(user), scope=scope
        )


def test_the_estimate_counts_every_page_twice_and_the_text() -> None:
    h = household()
    document, pages = sealed_document(h.workspace, h.anne, pages=2)
    estimate = export.estimate_bytes(h.workspace.id, None, [document.id])
    assert estimate >= 2 * sum(len(page) for page in pages)
    assert export.estimate_bytes(h.workspace.id, None, []) > 0


def test_an_export_over_the_limit_is_refused_when_asked(settings: Settings) -> None:
    h = household()
    sealed_document(h.workspace, h.anne, pages=1)
    settings.BRIEVIE_EXPORT_MAX_BYTES = 10
    with pytest.raises(ApiError) as caught:
        _ask(h.workspace, h.anne.id, "all")
    assert (caught.value.status, caught.value.code) == (409, "export_too_large")
    with as_actor(h.workspace, SYSTEM):
        assert not MemberExport.objects.exists()


def test_no_room_on_the_disk_fails_with_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    sealed_document(h.workspace, h.anne, pages=1)
    row = _ask(h.workspace, h.anne.id, "all")
    monkeypatch.setattr(member_export, "free_bytes", lambda: 0)
    member_export.build_export(h.workspace.id, row.id)
    with as_actor(h.workspace, SYSTEM):
        stored = MemberExport.objects.get(id=row.id)
    assert (stored.status, stored.failure_code, stored.size) == ("failed", "not_enough_space", 0)


def test_over_the_limit_at_build_time_fails_too_large(settings: Settings) -> None:
    h = household()
    sealed_document(h.workspace, h.anne, pages=1)
    row = _ask(h.workspace, h.anne.id, "mine")
    settings.BRIEVIE_EXPORT_MAX_BYTES = 10
    member_export.build_export(h.workspace.id, row.id)
    with as_actor(h.workspace, SYSTEM):
        stored = MemberExport.objects.get(id=row.id)
    assert (stored.status, stored.failure_code) == ("failed", "too_large")


def test_one_export_of_everything_at_a_time_per_workspace() -> None:
    h = household()
    sealed_document(h.workspace, h.anne, pages=1)
    _ask(h.workspace, h.anne.id, "all")
    with pytest.raises(ApiError) as caught:
        _ask(h.workspace, h.bas.id, "all")
    assert (caught.value.status, caught.value.code) == (409, "export_running_elsewhere")
    # A member's own archive is not held up by it.
    assert _ask(h.workspace, h.bas.id, "mine").status == "pending"


def test_a_documents_pages_are_read_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    document, _pages = sealed_document(h.workspace, h.anne, pages=3)
    opened: list[object] = []
    real = export._open_page

    def counting(*args: object, **kwargs: object) -> bytes:
        opened.append(args[2] if len(args) > 2 else kwargs.get("index"))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export, "_open_page", counting)
    export.write_export(
        workspace_id=h.workspace.id,
        reader_id=None,
        document_ids=[document.id],
        target=io.BytesIO(),
        title="Archief",
    )
    # Three pages, each opened for its picture, then again one by one for the PDF (no list
    # of a document's pages is held).
    assert opened == [1, 2, 3, 1, 2, 3]
