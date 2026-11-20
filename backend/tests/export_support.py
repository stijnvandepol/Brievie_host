"""Helpers for the export tests: documents with sealed pages, people, labels and tasks."""

import io
import json
import uuid
import zipfile
from datetime import date

from apps.accounts.models import User
from apps.documents import visibility
from apps.documents.intake import create_document_from_upload
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.documents.schemas import UploadMetadataIn
from apps.labels.models import Label
from apps.people.models import Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import jpeg, metadata_for


def sealed_document(
    ws: Workspace,
    user: User,
    *,
    pages: int = 2,
    seed: str = "",
    persons: list[Person] | None = None,
    labels: list[str] | None = None,
    tasks: list[tuple[str, str, date | None]] | None = None,
    sender: str = "",
    summary: str = "",
) -> tuple[Document, list[bytes]]:
    """A stored document with `pages` sealed page files; returns it and the plain pages."""
    seed = seed or uuid.uuid4().hex
    data = [jpeg(f"{seed}-{n}") for n in range(pages)]
    metadata = UploadMetadataIn.model_validate(metadata_for(data))
    with workspace_context(ws.id, actor=SYSTEM):
        document, _created = create_document_from_upload(
            workspace=ws, user=user, metadata=metadata, pages=data
        )
        document.sender = sender
        document.summary = summary
        document.status = Document.Status.DONE
        document.save()
        for person in persons or []:
            DocumentAddressee.objects.create(workspace=ws, document=document, person=person)
        visibility.sync_addressee_ids(document)
        for name in labels or []:
            label, _made = Label.objects.get_or_create(workspace=ws, name=name)
            DocumentLabel.objects.create(workspace=ws, document=document, label=label)
        for kind, description, deadline in tasks or []:
            Task.objects.create(
                workspace=ws,
                document=document,
                type=kind,
                description=description,
                deadline=deadline,
            )
    return document, data


def read_zip(payload: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(payload))


def export_json(archive: zipfile.ZipFile) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(archive.read("brievie-export.json"))
    return loaded


def export_documents(archive: zipfile.ZipFile) -> list[dict[str, object]]:
    documents = export_json(archive)["documents"]
    assert isinstance(documents, list)
    result: list[dict[str, object]] = []
    for entry in documents:
        assert isinstance(entry, dict)
        result.append(entry)
    return result
