"""Own documents (archive spec S 6): paper that is not post, added straight to the archive.

The one creator of a `kind = document` (`create_document`, called by the upload inside its
transaction) and the one place that checks what an own document may carry. The pure part
(`validate_fields`) needs no database; `check_names` looks up the persons and labels.

What the request may choose only narrows: `kind` and `status` are set by the server (a new
document is `processing`, the pipeline ends it `done`), `private` can only make it private.
Persons and labels go through `organise` (it writes the history), inside the document's
addressee scope; then, as the system, the addressee list and the readers (S 4.9.3); the
private flip last, in the same transaction.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final
from uuid import UUID

from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents import organise, visibility
from apps.documents.models import DOCUMENT_TYPES, REMIND_DAYS, Document, DocumentEvent
from apps.documents.schemas import UploadMetadataIn
from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.locks import lock_own_role
from apps.workspaces.models import Membership, Workspace

TITLE_MAX: Final = 200
# The same bounds as a letter's date in a review: a typo (2062, 0001) would land in lists.
DATE_FROM: Final = date(1900, 1, 1)
# A document's own date may be today (or tomorrow, a clock elsewhere); an end or retention
# date lies at most a hundred years ahead.
DATE_AHEAD: Final = timedelta(days=1)
FAR_AHEAD: Final = timedelta(days=366 * 100)
DEFAULT_REMIND_DAYS: Final = 30


@dataclass(frozen=True)
class OwnDocumentData:
    """What an own document's upload carries, checked."""

    title: str
    document_type: str
    document_date: date | None
    person_ids: tuple[UUID, ...]
    label_ids: tuple[UUID, ...]
    expires_on: date | None
    remind_days: int
    retain_until: date | None
    private: bool


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)


def validate_fields(metadata: UploadMetadataIn, *, today: date) -> OwnDocumentData | None:
    """The upload's archive fields checked without the database: None for a letter (an
    older app sends none of them), the data for an own document. 422 `validation_error`
    with a Dutch sentence that names no value."""
    if metadata.kind == Document.Kind.LETTER:
        if (
            metadata.title
            or metadata.document_type
            or metadata.document_date is not None
            or metadata.person_ids
            or metadata.label_ids
            or metadata.expires_on is not None
            or metadata.remind_days != DEFAULT_REMIND_DAYS
            or metadata.retain_until is not None
            or metadata.private
        ):
            raise _invalid(_("Deze gegevens horen bij een eigen document, niet bij een brief."))
        return None
    if metadata.kind != Document.Kind.DOCUMENT:
        raise _invalid(_("Kies een brief of een eigen document."))
    title = " ".join(metadata.title.split())
    if not title:
        raise _invalid(_("Een document heeft een titel nodig."))
    if len(title) > TITLE_MAX:
        raise _invalid(_("Een titel is hoogstens 200 tekens lang."))
    if metadata.document_type not in DOCUMENT_TYPES:
        raise _invalid(_("Kies een soort document uit de lijst."))
    check_dates(
        document_date=metadata.document_date,
        expires_on=metadata.expires_on,
        retain_until=metadata.retain_until,
        today=today,
    )
    if metadata.remind_days not in REMIND_DAYS:
        raise _invalid(_("Kies 7, 30, 60 of 90 dagen vooraf."))
    return OwnDocumentData(
        title=mask_bsns(title),
        document_type=metadata.document_type,
        document_date=metadata.document_date,
        person_ids=tuple(dict.fromkeys(metadata.person_ids)),
        label_ids=tuple(dict.fromkeys(metadata.label_ids)),
        expires_on=metadata.expires_on,
        remind_days=metadata.remind_days,
        retain_until=metadata.retain_until,
        private=metadata.private,
    )


def check_dates(
    *,
    document_date: date | None,
    expires_on: date | None,
    retain_until: date | None,
    today: date,
) -> None:
    """The dates of an own document are sane: its date not in the future, an end date not
    before it, and every date between 1900 and a hundred years ahead."""
    if document_date is not None and not (DATE_FROM <= document_date <= today + DATE_AHEAD):
        raise _invalid(_("De datum van een document ligt tussen 1 januari 1900 en vandaag."))
    for value in (expires_on, retain_until):
        if value is not None and not (DATE_FROM <= value <= today + FAR_AHEAD):
            raise _invalid(_("Deze datum ligt te ver weg."))
    if expires_on is not None and document_date is not None and expires_on < document_date:
        raise _invalid(_("Geldig tot ligt na de datum van het document."))


def check_names(workspace: Workspace, data: OwnDocumentData) -> None:
    """Every person exists in the workspace and is not archived, every label exists (both
    through the workspace's policies, as the caller); 422 otherwise, before anything is
    stored. The same persons a member may name in a review."""
    if data.person_ids:
        found = Person.objects.filter(
            workspace_id=workspace.id, id__in=data.person_ids, archived_at__isnull=True
        ).count()
        if found != len(data.person_ids):
            raise _invalid(_("Kies voor wie het document is uit de lijst van mensen."))
    if data.label_ids:
        found = Label.objects.filter(workspace_id=workspace.id, id__in=data.label_ids).count()
        if found != len(data.label_ids):
            raise _invalid(_("Kies de labels uit de lijst."))


def lock_creator(workspace: Workspace, user: User) -> None:
    """The first lock of adding an own document: the caller's membership, its role read
    under the lock (a member demoted to read-only meanwhile may not add: 403)."""
    if lock_own_role(workspace.id, user.id) == Membership.Role.VIEWER:
        raise ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))


def document_fields(data: OwnDocumentData) -> dict[str, object]:
    """The columns the new row is created with (the kind is the server's)."""
    return {
        "kind": Document.Kind.DOCUMENT,
        "title": data.title,
        "document_type": data.document_type,
        "letter_date": data.document_date,
        "expires_on": data.expires_on,
        "remind_days": data.remind_days,
        "retain_until": data.retain_until,
    }


def create_document(
    document: Document, data: OwnDocumentData, *, user: User, actor: AuditActor
) -> None:
    """Organise a just-created own document, as its uploader, in the upload's
    transaction: labels and persons through `organise`, the history line
    `document_added`, the addressee list and readers at the scope's end, then (when asked)
    the private flip with the uploader as a reader. Nothing here widens who reads it."""
    with visibility.keep_own_access(document), visibility.addressee_scope(document):
        organise.set_labels(document=document, label_ids=list(data.label_ids), actor=user)
        organise.set_addressees(document=document, person_ids=list(data.person_ids), actor=user)
        organise.record_document_added(document, actor=user)
    if data.private:
        visibility.make_private(document, actor=actor, origin=DocumentEvent.Origin.USER)
