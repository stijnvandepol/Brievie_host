"""Helpers for the archive tests: an own document written directly, as the system."""

import uuid
from collections.abc import Sequence
from datetime import date

from apps.accounts.models import User
from apps.documents.models import Document, DocumentAddressee, DocumentPage
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.visibility_support import as_actor


def own_document(
    workspace: Workspace,
    *,
    on: Sequence[Person] = (),
    scanned_by: User | None = None,
    title: str = "Paspoort",
    document_type: str = "identity",
    status: str = "done",
    expires_on: date | None = None,
    remind_days: int = 30,
    retain_until: date | None = None,
    letter_date: date | None = None,
    sender: str = "",
    ocr_text: str = "",
) -> Document:
    """An own document (`kind = document`) that is not private, with an addressee row per
    person and one page row, as the system; no task."""
    with as_actor(workspace, SYSTEM):
        document = Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            kind=Document.Kind.DOCUMENT,
            title=title,
            document_type=document_type,
            page_count=1,
            status=status,
            scanned_by=scanned_by,
            confirmed_by=scanned_by if status == "done" else None,
            expires_on=expires_on,
            remind_days=remind_days,
            retain_until=retain_until,
            letter_date=letter_date,
            sender=sender,
            ocr_text=ocr_text,
            addressee_ids=sorted(p.id for p in on),
        )
        for person in on:
            DocumentAddressee.objects.create(workspace=workspace, document=document, person=person)
        DocumentPage.objects.create(
            workspace=workspace,
            document=document,
            index=1,
            size=1,
            sha256="0" * 64,
            storage_path=f"test/{document.id}/1",
        )
    return document


def membership_of(workspace: Workspace, user: User) -> Membership:
    return Membership.objects.select_related("workspace").get(workspace=workspace, user=user)
