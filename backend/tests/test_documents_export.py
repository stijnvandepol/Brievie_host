"""The export building block: a ZIP of chosen documents, read through `export_context`."""

import io
from contextlib import AbstractContextManager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.db.backends.postgresql.base import DatabaseWrapper
from django.utils import timezone

from apps.common import crypto
from apps.documents import export
from apps.documents.models import Document
from apps.documents.storage import default_storage
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, make_person
from tests.export_support import export_documents, export_json, read_zip, sealed_document

TITLE = "Post van Oma"


def run_export(ws: Workspace, ids: list[UUID], *, reader: UUID | None = None) -> bytes:
    target = io.BytesIO()
    export.write_export(
        workspace_id=ws.id, reader_id=reader, document_ids=ids, target=target, title=TITLE
    )
    return target.getvalue()


def test_export_holds_only_the_given_documents_pages_and_data(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    wanted, pages = sealed_document(ws_a, admin, pages=2, sender="Belastingdienst")
    other, other_pages = sealed_document(ws_a, admin, pages=1, sender="Geheim")

    payload = run_export(ws_a, [wanted.id])

    archive = read_zip(payload)
    names = archive.namelist()
    assert sorted(names) == sorted(
        [
            f"pagina's/{wanted.id}/1.jpg",
            f"pagina's/{wanted.id}/2.jpg",
            "brievie-export.json",
            "LEESMIJ.txt",
        ]
    )
    assert archive.read(f"pagina's/{wanted.id}/1.jpg") == pages[0]
    assert archive.read(f"pagina's/{wanted.id}/2.jpg") == pages[1]
    assert str(other.id).encode() not in payload
    assert b"Geheim" not in archive.read("brievie-export.json")
    assert other_pages[0] not in payload
    assert archive.testzip() is None


def test_export_never_reads_another_workspaces_documents(ws_a: Workspace, ws_b: Workspace) -> None:
    foreign, _pages = sealed_document(ws_b, admin_of(ws_b), pages=1)

    payload = run_export(ws_a, [foreign.id])

    archive = read_zip(payload)
    assert sorted(archive.namelist()) == ["LEESMIJ.txt", "brievie-export.json"]
    assert export_documents(archive) == []


def test_export_json_names_persons_labels_and_tasks(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    oma = make_person(ws_a, "Oma")
    opa = make_person(ws_a, "Opa")
    document, _pages = sealed_document(
        ws_a,
        admin,
        pages=1,
        persons=[opa, oma],
        labels=["Belasting", "Auto"],
        tasks=[("pay", "Betaal de aanslag", timezone.now().date())],
        sender="Belastingdienst",
        summary="Aanslag 2025",
    )

    archive = read_zip(run_export(ws_a, [document.id]))

    data = export_json(archive)
    assert data["version"] == 1
    (entry,) = export_documents(archive)
    assert entry["id"] == str(document.id)
    assert entry["status"] == "done"
    assert entry["sender"] == "Belastingdienst"
    assert entry["summary"] == "Aanslag 2025"
    assert entry["summary_by_ai"] is False
    assert entry["document_type"] == ""
    assert entry["letter_date"] == ""
    assert str(entry["created_at"]).startswith("20")
    assert entry["persons"] == ["Oma", "Opa"]
    assert entry["labels"] == ["Auto", "Belasting"]
    assert entry["tasks"] == [
        {
            "type": "pay",
            "description": "Betaal de aanslag",
            "deadline": timezone.now().date().isoformat(),
            "status": "open",
        }
    ]
    assert entry["pages"] == [f"pagina's/{document.id}/1.jpg"]
    # The text of the letter itself is not part of the data file.
    assert "ocr_text" not in entry


def test_readme_says_what_is_in_it(ws_a: Workspace) -> None:
    document, _pages = sealed_document(ws_a, admin_of(ws_a), pages=2)

    archive = read_zip(run_export(ws_a, [document.id]))

    text = archive.read("LEESMIJ.txt").decode()
    assert TITLE in text
    assert "BSN" in text
    assert "niet versleuteld" in text
    assert "Aantal brieven: 1" in text
    assert "Aantal pagina's: 2" in text


def test_counts_are_returned(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    one, _p = sealed_document(ws_a, admin, pages=2)
    two, _p = sealed_document(ws_a, admin, pages=3)
    target = io.BytesIO()

    counts = export.write_export(
        workspace_id=ws_a.id,
        reader_id=None,
        document_ids=[one.id, two.id, one.id, uuid4()],
        target=target,
        title=TITLE,
    )

    assert counts == export.ExportCounts(documents=2, pages=5)
    # A repeated id is one document.
    assert len(export_documents(read_zip(target.getvalue()))) == 2


@pytest.mark.django_db(transaction=True)
def test_write_export_opens_a_short_context_per_document(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = admin_of(ws_a)
    ids = [sealed_document(ws_a, admin, pages=2)[0].id for _ in range(3)]
    assert not connection.in_atomic_block
    commits: list[bool] = []
    real_commit = DatabaseWrapper.commit

    def spy_commit(self: DatabaseWrapper) -> None:
        commits.append(True)
        real_commit(self)

    open_during_page_reads: list[bool] = []
    real_open = export._open_page

    def spy_open(*args: object, **kwargs: object) -> bytes:
        open_during_page_reads.append(connection.in_atomic_block)
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(DatabaseWrapper, "commit", spy_commit)
    monkeypatch.setattr(export, "_open_page", spy_open)

    run_export(ws_a, ids)

    # One transaction per document, and none open while a page file is read.
    assert len(commits) == 3
    assert open_during_page_reads == [False] * 6
    assert not connection.in_atomic_block


def test_a_document_deleted_meanwhile_is_skipped(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    kept, kept_pages = sealed_document(ws_a, admin, pages=1)
    gone, _pages = sealed_document(ws_a, admin, pages=1)
    with workspace_context(ws_a.id):
        Document.objects.filter(id=gone.id).delete()

    archive = read_zip(run_export(ws_a, [gone.id, kept.id]))

    assert str(gone.id) not in " ".join(archive.namelist())
    assert [e["id"] for e in export_documents(archive)] == [str(kept.id)]
    assert archive.read(f"pagina's/{kept.id}/1.jpg") == kept_pages[0]


def test_a_document_whose_page_file_vanished_while_reading_is_skipped(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = admin_of(ws_a)
    kept, _kept_pages = sealed_document(ws_a, admin, pages=1)
    gone, _pages = sealed_document(ws_a, admin, pages=1)
    real = export._open_page

    def vanishing(*args: object, **kwargs: object) -> bytes:
        document_id = args[1] if len(args) > 1 else kwargs["document_id"]
        if document_id == gone.id:
            # Deleted between reading its rows and reading its file.
            with workspace_context(ws_a.id):
                Document.objects.filter(id=gone.id).delete()
            raise FileNotFoundError("gone")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export, "_open_page", vanishing)

    archive = read_zip(run_export(ws_a, [gone.id, kept.id]))

    assert [e["id"] for e in export_documents(archive)] == [str(kept.id)]


def test_a_missing_file_of_a_document_that_still_exists_is_an_error(ws_a: Workspace) -> None:
    document, _pages = sealed_document(ws_a, admin_of(ws_a), pages=1)
    with workspace_context(ws_a.id):
        path = document.pages.get().storage_path
    default_storage().delete(path)

    with pytest.raises(FileNotFoundError):
        run_export(ws_a, [document.id])


def test_a_page_that_does_not_open_is_an_error_not_a_blank(ws_a: Workspace) -> None:
    document, _pages = sealed_document(ws_a, admin_of(ws_a), pages=1)
    with workspace_context(ws_a.id):
        path = document.pages.get().storage_path
    default_storage().write(path, crypto.seal(bytes(32), b"other key", aad=b"x"))

    with pytest.raises(crypto.DecryptionError):
        run_export(ws_a, [document.id])


def test_the_reader_is_handed_to_the_context(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, _pages = sealed_document(ws_a, admin_of(ws_a), pages=1)
    seen: list[UUID | None] = []
    real = export.export_context

    def spy(workspace_id: UUID, *, reader_id: UUID | None) -> AbstractContextManager[None]:
        seen.append(reader_id)
        return real(workspace_id, reader_id=reader_id)

    monkeypatch.setattr(export, "export_context", spy)
    reader = uuid4()

    run_export(ws_a, [document.id], reader=reader)

    assert seen == [reader]


def test_every_export_read_goes_through_export_context() -> None:
    root = Path(export.__file__).parent
    source = (root / "export.py").read_text()
    start = source.index("def export_context")
    end = source.index("\n\n\n", start)

    # `workspace_context(` is called once, inside `export_context` itself.
    assert source.count("workspace_context(") == source[start:end].count("workspace_context(") == 1
    for name in ("member_export.py", "jobs.py"):
        later = root / name
        if later.exists():
            assert "workspace_context(" not in later.read_text(), name
