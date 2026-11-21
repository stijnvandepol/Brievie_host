"""The member's own export reads as the member (S 4.10, 4.18, plan Task 20, PB T13).

It holds the letters on the member's own name and the private letters they read through
a reader row; never more because a switch lets them see more, never a letter they may
not read, and nothing at all for someone who is no member any more.
"""

import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.documents import member_export
from apps.documents.models import DocumentReader, MemberExport
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.visibility_support import Office, as_actor, office, restrict_raw


def _ids(o: Office, name: str) -> set[str]:
    user = o.users[name]
    return {o.name_of(i) for i in member_export.member_document_ids(o.workspace.id, user.id)}


def _reader(o: Office, letter: str, user_name: str, **fields: object) -> None:
    with as_actor(o.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=o.workspace,
            document=o.letters[letter],
            user=o.users[user_name],
            **fields,
        )


def test_own_export_holds_own_post_and_private_letters_read() -> None:
    o = office()
    # Femke: her letters, not Finance's (a department's post is no own post).
    assert _ids(o, "finance") == {"scanned_in_review", "private_femke"}
    assert _ids(o, "plain") == {"own_piet"}
    assert _ids(o, "hr") == set()

    # Femke shares her private letter with Hans: it is his to export too.
    _reader(o, "private_femke", "hr", reason=DocumentReader.Reason.SHARE)
    assert _ids(o, "hr") == {"private_femke"}


def test_a_lapsed_or_emergency_reader_row_adds_nothing() -> None:
    o = office()
    _reader(
        o,
        "private_femke",
        "hr",
        reason=DocumentReader.Reason.SHARE,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    _reader(
        o,
        "private_femke",
        "admin",
        reason=DocumentReader.Reason.EMERGENCY,
        expires_at=timezone.now() + timedelta(days=1),
    )
    assert _ids(o, "hr") == set()
    assert _ids(o, "admin") == set()


def test_own_export_does_not_grow_with_everyone_sees_all() -> None:
    o = office()
    before = {name: _ids(o, name) for name in o.users}
    o.state(e=True, d=True, a=True)
    assert "finance_letter" in o.visible(o.plain.id)
    assert {name: _ids(o, name) for name in o.users} == before
    o.state(e=False, d=False, a=True, s=False)
    assert {name: _ids(o, name) for name in o.users} == before


def test_own_export_omits_letters_the_member_cannot_read() -> None:
    o = office()
    # Piet's letter made private for Femke only: on his name, yet not his to read.
    restrict_raw(o.letters["own_piet"], [o.finance])
    assert "own_piet" not in o.visible(o.plain.id)
    assert _ids(o, "plain") == set()
    assert "own_piet" in _ids(o, "finance")


def test_export_job_for_a_member_who_left_writes_nothing() -> None:
    o = office()
    export_id = uuid.uuid4()
    with as_actor(o.workspace, SYSTEM):
        export = MemberExport.objects.create(
            id=export_id,
            workspace=o.workspace,
            user=o.plain,
            storage_path=f"exports/{o.workspace.id}/{export_id}.sealed",
        )
        Membership.objects.filter(workspace=o.workspace, user=o.plain).delete()

    member_export.build_export(o.workspace.id, export_id)

    assert not (Path(settings.BRIEVIE_STORAGE_ROOT) / export.storage_path).exists()
    with as_actor(o.workspace, SYSTEM):
        stored = MemberExport.objects.get(id=export_id)
    assert stored.status == MemberExport.Status.FAILED
    assert stored.document_count == 0
    # Read as Piet now (no member): no letter at all.
    assert member_export.member_document_ids(o.workspace.id, o.plain.id) == []
