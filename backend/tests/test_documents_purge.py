"""The purge of the trash (`apps.documents.purge`): expiry, the three steps per letter and
the run of every workspace under the back-up lock.

Deleting code: real sealed page files are made by the upload, only under this test's
`tmp_path` (the autouse fixture in conftest sets `BRIEVIE_STORAGE_ROOT` to
`tmp_path / "documents"` and installs the test root). `_storage_in_tmp` proves that and
that the guard refuses a root outside it, before every test here.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.utils import timezone

from apps.audit.models import WorkspaceAuditEvent
from apps.backup import safepaths
from apps.backup.locks import BACKUP_LOCK_KEY
from apps.documents import purge
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentReader,
    DocumentReadMark,
    DocumentShare,
    DocumentSuggestion,
    Extraction,
)
from apps.documents.page_files import PageFileRefused, judge_storage_root, remove_document_files
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules.models import RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, make_document, trashed, upload
from tests.helpers import post_json
from tests.members_support import as_user, workspace_rows
from tests.visibility_support import office

SENDER = "Belastingdienst"


@pytest.fixture(autouse=True)
def _storage_in_tmp(tmp_path: Path) -> None:
    """The guard first: the root is this test's tmp_path, and anything else is refused."""
    root = Path(settings.BRIEVIE_STORAGE_ROOT)
    assert root == tmp_path / "documents"
    assert safepaths.installed_test_root() == tmp_path.resolve()
    for outside in (Path("/data/documents"), Path("/"), tmp_path):
        with pytest.raises(PageFileRefused):
            judge_storage_root(outside)
    assert judge_storage_root(root) == root


def storage_root() -> Path:
    return Path(settings.BRIEVIE_STORAGE_ROOT)


def letter_with_pages(ws: Workspace, seed: str = "a") -> Document:
    """A letter made by the upload: two sealed page files under tmp_path."""
    client = Client()
    client.force_login(admin_of(ws))
    r = upload(client, ws.id, [jpeg(f"{seed}-1"), jpeg(f"{seed}-2")])
    assert r.status_code == 201, r.content
    with workspace_context(ws.id, actor=SYSTEM):
        document = Document.objects.get(id=r.json()["id"])
        Extraction.objects.create(
            workspace=ws,
            document=document,
            field=Extraction.Field.SENDER,
            value=SENDER,
            confidence=Decimal("0.900"),
        )
    return document


def letter_dir(document: Document) -> Path:
    return storage_root() / str(document.workspace_id) / str(document.id)


def fresh(document: Document) -> Document | None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.filter(id=document.id).first()


def count(model: type, document: Document) -> int:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return int(model.objects.filter(document_id=document.id).count())  # type: ignore[attr-defined]


def mark(document: Document) -> None:
    assert purge.mark_expired(document.workspace_id, now=timezone.now()) >= 1
    current = fresh(document)
    assert current is not None
    assert current.purge_started_at is not None


def test_mark_expired_marks_only_older_than_30_days(ws_a: Workspace) -> None:
    old = trashed(make_document(ws_a), days_ago=31)
    young = trashed(make_document(ws_a), days_ago=29)
    live = make_document(ws_a)

    assert purge.mark_expired(ws_a.id, now=timezone.now()) == 1

    assert fresh(old).purge_started_at is not None  # type: ignore[union-attr]
    assert fresh(young).purge_started_at is None  # type: ignore[union-attr]
    assert fresh(live).purge_started_at is None  # type: ignore[union-attr]
    [row] = workspace_rows(ws_a, "document_purged")
    assert (row.actor_kind, row.target_kind, row.target_id) == ("system", "document", old.id)
    assert row.values == {"reason": "expired"}
    assert row.target_name == ""
    # Marking again finds nothing new.
    assert purge.mark_expired(ws_a.id, now=timezone.now()) == 0


@pytest.mark.django_db(transaction=True)
def test_purge_removes_rows_files_and_tasks(ws_a: Workspace) -> None:
    document = letter_with_pages(ws_a, "gone")
    other = letter_with_pages(ws_a, "stays")
    assert sorted(p.name for p in letter_dir(document).iterdir()) == ["1.page", "2.page"]
    with workspace_context(ws_a.id, actor=SYSTEM):
        Task.objects.create(workspace=ws_a, document=document, type="pay", description="Betalen")
        Task.objects.create(
            workspace=ws_a, document=document, type="pay", description="Klaar", status="done"
        )
        Task.objects.create(workspace=ws_a, document=other, type="pay", description="Blijft")
    trashed(document, days_ago=31)
    mark(document)

    result = purge.purge_marked(ws_a.id)

    assert result == purge.PurgeResult(purged=1, refused=0)
    assert fresh(document) is None
    for model in (DocumentPage, DocumentEvent, Task, Extraction):
        assert count(model, document) == 0, model
    assert not letter_dir(document).exists()
    assert letter_dir(document).parent.is_dir()
    assert sorted(p.name for p in letter_dir(other).iterdir()) == ["1.page", "2.page"]
    assert fresh(other) is not None
    assert count(Task, other) == 1


@pytest.mark.django_db(transaction=True)
def test_purge_removes_everything_hanging_off() -> None:
    o = office()
    ws = o.workspace
    private = o.letters["private_femke"]
    neighbours = [d for name, d in o.letters.items() if name != "private_femke"]
    with workspace_context(ws.id, actor=SYSTEM):
        DocumentLabel.objects.filter(document=private).update(automatic=True)
        DocumentAddressee.objects.filter(document=private).update(automatic=True)
        Document.objects.filter(id=private.id).update(has_automatic_link=True)
        # The office's page rows are in a test form; give the letter its real page path.
        DocumentPage.objects.filter(document=private).update(
            storage_path=f"{ws.id}/{private.id}/1.page"
        )
        before = {
            d.id: (d.has_automatic_link, d.addressee_ids)
            for d in Document.objects.filter(id__in=[n.id for n in neighbours])
        }
        persons = Person.objects.count()
        labels = Label.objects.count()
        assert DocumentReader.objects.filter(document=private).exists()
        assert DocumentShare.objects.filter(document=private).exists()
        assert RuleRun.objects.filter(document=private).exists()
        assert DocumentSuggestion.objects.filter(document=private).exists()
        DocumentReadMark.objects.create(
            workspace=ws, document=private, user=o.finance, read_at=timezone.now()
        )
    # The workspace folder exists (a mounted share); the letter's own folder may be gone.
    (storage_root() / str(ws.id)).mkdir(parents=True)
    trashed(private, by=o.finance, days_ago=31)
    mark(private)

    assert purge.purge_marked(ws.id) == purge.PurgeResult(purged=1, refused=0)

    with workspace_context(ws.id, actor=SYSTEM):
        assert not Document.objects.filter(id=private.id).exists()
        for model in (
            DocumentAddressee,
            DocumentLabel,
            DocumentSuggestion,
            DocumentShare,
            DocumentReader,
            DocumentReadMark,
            RuleRun,
            DocumentEvent,
            DocumentPage,
            Extraction,
            Task,
        ):
            assert not model.objects.filter(document_id=private.id).exists(), model
        after = {
            d.id: (d.has_automatic_link, d.addressee_ids)
            for d in Document.objects.filter(id__in=[n.id for n in neighbours])
        }
        assert after == before
        assert Person.objects.count() == persons
        assert Label.objects.count() == labels


@pytest.mark.django_db(transaction=True)
def test_mark_expired_reaches_private_letters() -> None:
    o = office()
    ws = o.workspace
    private = o.letters["private_femke"]
    with workspace_context(ws.id, actor=SYSTEM):
        DocumentPage.objects.filter(document=private).update(
            storage_path=f"{ws.id}/{private.id}/1.page"
        )
    (storage_root() / str(ws.id)).mkdir(parents=True)
    trashed(private, by=o.finance, days_ago=31)

    purge.purge_all(now=timezone.now())

    assert fresh(private) is None
    [row] = workspace_rows(ws, "document_purged")
    assert (row.target_id, row.actor_kind, row.values) == (
        private.id,
        "system",
        {"reason": "expired"},
    )
    assert all(fresh(d) is not None for n, d in o.letters.items() if n != "private_femke")


@pytest.mark.django_db(transaction=True)
def test_purge_resumes_after_files_removed(ws_a: Workspace) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)
    mark(document)
    with workspace_context(ws_a.id, actor=SYSTEM):
        paths = list(
            DocumentPage.objects.filter(document=document).values_list("storage_path", flat=True)
        )
    # A run that removed the files and then crashed before the rows went.
    assert remove_document_files(ws_a.id, document.id, paths) == 2
    assert fresh(document) is not None

    assert purge.purge_marked(ws_a.id) == purge.PurgeResult(purged=1, refused=0)
    assert fresh(document) is None


@pytest.mark.parametrize(
    "error",
    [PageFileRefused("Not a page file of this letter."), PermissionError(13, "denied", "/x")],
)
@pytest.mark.django_db(transaction=True)
def test_purge_refusal_keeps_row_marked(
    ws_a: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)
    mark(document)

    def refuse(*args: object) -> int:
        raise error

    monkeypatch.setattr(purge, "remove_document_files", refuse)
    with caplog.at_level(logging.DEBUG):
        result = purge.purge_marked(ws_a.id)

    assert result == purge.PurgeResult(purged=0, refused=1)
    current = fresh(document)
    assert current is not None
    assert current.purge_started_at is not None
    assert sorted(p.name for p in letter_dir(document).iterdir()) == ["1.page", "2.page"]
    admin = as_user(admin_of(ws_a))
    assert admin.get(f"/api/v1/workspaces/{ws_a.id}/trash").json()["items"] == []
    assert SENDER not in caplog.text
    assert str(storage_root()) not in caplog.text
    assert "/x" not in caplog.text
    assert type(error).__name__ in caplog.text


@pytest.mark.django_db(transaction=True)
def test_restore_after_mark_is_refused(ws_a: Workspace) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)
    mark(document)
    admin = as_user(admin_of(ws_a))

    r = post_json(admin, f"/workspaces/{ws_a.id}/documents/{document.id}/restore")

    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"
    current = fresh(document)
    assert current is not None
    assert current.trashed_at is not None
    assert purge.purge_marked(ws_a.id).purged == 1
    assert not letter_dir(document).exists()


@pytest.fixture
def backup_running() -> Iterator[None]:
    other = psycopg.connect(
        f"dbname={connection.settings_dict['NAME']} user={connection.settings_dict['USER']} "
        f"password={connection.settings_dict['PASSWORD']} host={connection.settings_dict['HOST']} "
        f"port={connection.settings_dict['PORT']}",
        autocommit=True,
    )
    try:
        other.execute("SELECT pg_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        yield
    finally:
        other.close()


@pytest.mark.django_db(transaction=True)
def test_purge_all_skips_when_backup_lock_busy(ws_a: Workspace, backup_running: None) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)

    purge.purge_all(now=timezone.now())  # no exception

    current = fresh(document)
    assert current is not None
    assert current.purge_started_at is None
    assert sorted(p.name for p in letter_dir(document).iterdir()) == ["1.page", "2.page"]
    assert not WorkspaceAuditEvent.objects.filter(code="document_purged").exists()


@pytest.mark.django_db(transaction=True)
def test_purge_leaves_live_and_fresh_trash_alone(ws_a: Workspace, ws_b: Workspace) -> None:
    live = letter_with_pages(ws_a, "live")
    fresh_trash = trashed(letter_with_pages(ws_a, "fresh"), days_ago=29)
    old_a = trashed(letter_with_pages(ws_a, "old-a"), days_ago=31)
    old_b = trashed(letter_with_pages(ws_b, "old-b"), days_ago=40)

    purge.purge_all(now=timezone.now())

    assert fresh(old_a) is None
    assert fresh(old_b) is None
    assert not letter_dir(old_a).exists()
    assert not letter_dir(old_b).exists()
    for kept in (live, fresh_trash):
        current = fresh(kept)
        assert current is not None
        assert current.purge_started_at is None
        assert sorted(p.name for p in letter_dir(kept).iterdir()) == ["1.page", "2.page"]


@pytest.mark.django_db(transaction=True)
def test_purge_marked_takes_at_most_limit(ws_a: Workspace) -> None:
    letters = [trashed(letter_with_pages(ws_a, f"n{i}"), days_ago=31) for i in range(3)]
    purge.mark_expired(ws_a.id, now=timezone.now())

    assert purge.purge_marked(ws_a.id, limit=2) == purge.PurgeResult(purged=2, refused=0)
    assert sum(1 for d in letters if fresh(d) is not None) == 1
    assert purge.purge_marked(ws_a.id, limit=2) == purge.PurgeResult(purged=1, refused=0)


@pytest.mark.django_db(transaction=True)
def test_purge_marked_never_reaches_another_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    other = trashed(letter_with_pages(ws_b, "b"), days_ago=31)
    purge.mark_expired(ws_b.id, now=timezone.now())

    assert purge.purge_marked(ws_a.id) == purge.PurgeResult(purged=0, refused=0)
    assert fresh(other) is not None
    assert letter_dir(other).is_dir()


@pytest.mark.django_db(transaction=True)
def test_purge_of_a_letter_without_page_dir_is_fine(ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a), days_ago=31)
    mark(document)

    assert purge.purge_marked(ws_a.id) == purge.PurgeResult(purged=1, refused=0)
    assert fresh(document) is None


@pytest.mark.django_db(transaction=True)
def test_unplaceable_stored_path_is_refused_and_kept(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    document = letter_with_pages(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentPage.objects.filter(document=document, index=1).update(
            storage_path=f"{ws_a.id}/{uuid.uuid4()}/1.page"
        )
    trashed(document, days_ago=31)
    mark(document)

    assert purge.purge_marked(ws_a.id) == purge.PurgeResult(purged=0, refused=1)
    assert fresh(document) is not None
    assert sorted(p.name for p in letter_dir(document).iterdir()) == ["1.page", "2.page"]
    assert "PageFileRefused" in caplog.text


def unplaceable(document: Document) -> None:
    """A page row in another form: the guard refuses the whole letter."""
    with workspace_context(document.workspace_id, actor=SYSTEM):
        DocumentPage.objects.filter(document=document, index=1).update(
            storage_path=f"{document.workspace_id}/{uuid.uuid4()}/1.page"
        )


@pytest.mark.django_db(transaction=True)
def test_purge_refuses_pages_when_the_workspace_folder_is_missing(
    ws_a: Workspace, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)
    mark(document)
    # The share is not mounted: the workspace folder is elsewhere (under tmp_path).
    unmounted = tmp_path / "unmounted"
    (storage_root() / str(ws_a.id)).rename(unmounted)

    assert purge.purge_marked(ws_a.id) == purge.PurgeResult(purged=0, refused=1)

    current = fresh(document)
    assert current is not None
    assert current.purge_started_at is not None
    assert sorted(p.name for p in (unmounted / str(document.id)).iterdir()) == [
        "1.page",
        "2.page",
    ]
    assert "PageFileRefused" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_one_run_tries_a_refused_letter_once_and_reaches_the_rest(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(purge, "PURGE_BATCH", 2)
    broken = [letter_with_pages(ws_a, f"broken-{i}") for i in range(2)]
    good = letter_with_pages(ws_a, "good")
    for document in broken:
        unplaceable(document)
    for document in (*broken, good):
        trashed(document, days_ago=31)
    purge.mark_expired(ws_a.id, now=timezone.now())
    # The refused letters come first in every batch.
    with workspace_context(ws_a.id, actor=SYSTEM):
        for hours, document in ((3, broken[0]), (2, broken[1]), (1, good)):
            Document.objects.filter(id=document.id).update(
                purge_started_at=timezone.now() - timedelta(hours=hours)
            )
    tried: list[uuid.UUID] = []
    real = remove_document_files

    def counting(workspace_id: uuid.UUID, document_id: uuid.UUID, paths: list[str]) -> int:
        tried.append(document_id)
        return real(workspace_id, document_id, paths)

    monkeypatch.setattr(purge, "remove_document_files", counting)

    purge.purge_workspace(ws_a.id)

    assert fresh(good) is None
    assert all(fresh(document) is not None for document in broken)
    assert sorted(tried) == sorted(d.id for d in (*broken, good))
    assert caplog.text.count("PageFileRefused") == 2


@pytest.mark.django_db(transaction=True)
def test_purge_all_goes_on_after_one_workspace_fails(
    ws_a: Workspace,
    ws_b: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing, other = sorted((ws_a, ws_b), key=lambda ws: ws.id)
    old = trashed(letter_with_pages(other, "other"), days_ago=31)
    real = purge.mark_expired

    def mark_or_fail(workspace_id: uuid.UUID, *, now: datetime) -> int:
        if workspace_id == failing.id:
            raise DatabaseError("relation details 1234")
        return real(workspace_id, now=now)

    monkeypatch.setattr(purge, "mark_expired", mark_or_fail)

    purge.purge_all(now=timezone.now())

    assert fresh(old) is None
    assert not letter_dir(old).exists()
    assert "DatabaseError" in caplog.text
    assert str(failing.id) in caplog.text
    assert "relation details" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_purge_marked_refuses_inside_a_transaction(ws_a: Workspace) -> None:
    document = letter_with_pages(ws_a)
    trashed(document, days_ago=31)
    mark(document)

    with pytest.raises(RuntimeError), transaction.atomic():
        purge.purge_marked(ws_a.id)

    current = fresh(document)
    assert current is not None
    assert current.purge_started_at is not None
    assert sorted(p.name for p in letter_dir(document).iterdir()) == ["1.page", "2.page"]
