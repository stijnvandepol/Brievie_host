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

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents import organise, reminders, visibility
from apps.documents.models import (
    DOCUMENT_TYPES,
    REMIND_DAYS,
    SENSITIVE_TYPES,
    Document,
    DocumentEvent,
)
from apps.documents.schemas import DocumentDetailsIn, UploadMetadataIn
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
# "Bewaren tot" stops a permanent delete: at most this far ahead (security review of PR #54,
# point 3; the longest Dutch term people meet, a medical file, is 20 years).
RETAIN_AHEAD: Final = timedelta(days=366 * 20)
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
        # Besluit B3 on the server too (security review of PR #54, point 8): an app that
        # sends nothing gets the sensitive types private; it can always say false.
        private=metadata.private
        if metadata.private is not None
        else metadata.document_type in SENSITIVE_TYPES,
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
    if retain_until is not None and retain_until > today + RETAIN_AHEAD:
        raise _invalid(_("Bewaren tot ligt hoogstens 20 jaar vooruit."))
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


def lock_creator(workspace: Workspace, user: User) -> str:
    """The first lock of adding an own document: the caller's membership, its role read
    under the lock (a member demoted to read-only meanwhile may not add: 403). The role."""
    role = lock_own_role(workspace.id, user.id)
    if role == Membership.Role.VIEWER:
        raise ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))
    return role


def record_retention(document: Document, *, actor: AuditActor) -> None:
    """The audit row of a changed "Bewaren tot": the date only, the document by its id (no
    title or text), so the change outlives a purge of the document and its history."""
    record(
        AuditCode.RETENTION_CHANGED,
        actor=actor,
        target=AuditTarget(TargetKind.DOCUMENT, document.id),
        workspace_id=document.workspace_id,
        values={"until": document.retain_until.isoformat() if document.retain_until else ""},
    )


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
    if data.retain_until is not None:
        record_retention(document, actor=actor)
    if data.private:
        visibility.make_private(document, actor=actor, origin=DocumentEvent.Origin.USER)


def _locked_for_edit(
    membership: Membership, user: User, document_id: UUID, *, only_clears_retention: bool
) -> Document:
    """The document the member sees, locked for a change: the caller's membership first (its
    role read under the lock: a member demoted to read-only meanwhile gets 403), then the
    document. 404 `document_not_found` for one they may not see or that is in the trash;
    403 `emergency_read_only` when they see it only through emergency access.

    One exception (security review of PR #54, point 3): an admin may take "Bewaren tot"
    away from a document in their trash (`only_clears_retention`), so purging it, and an
    erasure request, is never blocked by another member's date."""
    role = lock_creator(membership.workspace, user)
    document = (
        visibility.visible_documents(membership)
        .select_for_update(no_key=True, of=("self",))
        .filter(id=document_id)
        .first()
    )
    if document is None and only_clears_retention and role == Membership.Role.ADMIN:
        document = (
            visibility.trashed_documents(membership)
            .select_for_update(no_key=True, of=("self",))
            .filter(id=document_id)
            .first()
        )
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    visibility.require_editable(document)
    return document


def update_details(
    *,
    membership: Membership,
    user: User,
    actor: AuditActor,
    document_id: UUID,
    data: DocumentDetailsIn,
) -> Document:
    """Change an own document's details, or a letter's "Bewaren tot" (archive spec S 10).

    One history line per changed field (`details_changed`: the field's name, and the new
    date for a date; never the title), `retention_changed` for "Bewaren tot". Nothing that
    is sent unchanged writes a line."""
    with transaction.atomic():
        only_clears_retention = data.clear_retain_until and data == DocumentDetailsIn(
            clear_retain_until=True
        )
        document = _locked_for_edit(
            membership, user, document_id, only_clears_retention=only_clears_retention
        )
        is_document = document.kind == Document.Kind.DOCUMENT
        if not is_document and (
            data.title
            or data.document_type
            or data.document_date is not None
            or data.expires_on is not None
            or data.clear_expires_on
            or data.remind_days
        ):
            raise _invalid(_("Bij een brief kun je alleen Bewaren tot aanpassen."))
        if data.expires_on is not None and data.clear_expires_on:
            raise _invalid(_("Kies een nieuwe datum of haal de datum weg, niet allebei."))
        if data.retain_until is not None and data.clear_retain_until:
            raise _invalid(_("Kies een nieuwe datum of haal de datum weg, niet allebei."))
        title = " ".join(data.title.split())
        if len(title) > TITLE_MAX:
            raise _invalid(_("Een titel is hoogstens 200 tekens lang."))
        if data.document_type and data.document_type not in DOCUMENT_TYPES:
            raise _invalid(_("Kies een soort document uit de lijst."))
        if data.remind_days and data.remind_days not in REMIND_DAYS:
            raise _invalid(_("Kies 7, 30, 60 of 90 dagen vooraf."))
        wanted: dict[str, object] = {}
        if title:
            wanted["title"] = mask_bsns(title)
        if data.document_type:
            wanted["document_type"] = data.document_type
        if data.document_date is not None:
            wanted["letter_date"] = data.document_date
        if data.expires_on is not None or data.clear_expires_on:
            wanted["expires_on"] = data.expires_on
        if data.remind_days:
            wanted["remind_days"] = data.remind_days
        if data.retain_until is not None or data.clear_retain_until:
            wanted["retain_until"] = data.retain_until
        # A letter's own date is the review's (up to a year ahead): only an own document's
        # dates are judged here.
        check_dates(
            document_date=_new(wanted, "letter_date", document.letter_date)
            if is_document
            else None,
            expires_on=_new(wanted, "expires_on", document.expires_on),
            retain_until=_new(wanted, "retain_until", document.retain_until),
            today=timezone.localdate(),
        )
        changed = [name for name, value in wanted.items() if getattr(document, name) != value]
        if not changed:
            return document
        for name in changed:
            setattr(document, name, wanted[name])
        document.save(update_fields=[*changed, "updated_at"])
        if "expires_on" in changed or "title" in changed:
            # The open reminder follows the new end date and title, or ends with a cleared
            # date.
            reminders.on_details_changed(document)
        for name in changed:
            if name == "retain_until":
                organise.record_retention_changed(document, actor=user, until=document.retain_until)
                record_retention(document, actor=actor)
                continue
            value = getattr(document, name)
            organise.record_details_changed(
                document,
                actor=user,
                field="document_date" if name == "letter_date" else name,
                on_date=value if isinstance(value, date) else None,
            )
    return document


def _new(wanted: dict[str, object], name: str, current: date | None) -> date | None:
    """The date a field will have: the wanted one when it changes, else the current one."""
    if name not in wanted:
        return current
    value = wanted[name]
    return value if isinstance(value, date) else None
