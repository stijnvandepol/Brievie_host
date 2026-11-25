"""What a member has read: "Ongelezen" (navigation spec S 5).

The only writer of `DocumentReadMark`. A mark is one member's fact about one letter: it is
written as that member (the own-row policy refuses anything else), never put in the
letter's history, the audit log, an export or a log line, and never shown to anybody else.

A letter is unread for a member when they see it, it is not being processed, it arrived
after their `Membership.unread_from`, and they have no mark for it (S 5.2). A letter is read
when they open it (an explicit call of the web's letter page or the app's reader, never a
plain `GET`), scan or upload it, or mark it read (S 5.3).
"""

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Exists, Min, OuterRef, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import visibility
from apps.documents.models import LETTERS_ONLY, Document, DocumentReadMark
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import current_actor, system_actor
from apps.workspaces.models import Membership, Workspace

# "Alles gelezen" marks the rows on screen: a list shows at most 200 (S 5.3).
MARK_MANY_MAX: Final = 200


def unread_q(membership: Membership) -> Q:
    """The condition of an unread letter for `membership` (S 5.2). Only ever applied on top
    of `visibility.visible_documents(membership)`: it narrows, it never widens. Letters
    only: an own document is never unread (archive spec S 8, AR8)."""
    marked = DocumentReadMark.objects.filter(document=OuterRef("pk"), user_id=membership.user_id)
    return (
        LETTERS_ONLY
        & ~Q(status=Document.Status.PROCESSING)
        & Q(created_at__gte=membership.unread_from)
        & Q(~Exists(marked))
    )


def unread_ids(membership: Membership, documents: Sequence[Document]) -> set[UUID]:
    """Which of `documents` (already read through the seam) are unread for the member: one
    query for a whole list."""
    candidates = [
        d.id
        for d in documents
        if d.kind == Document.Kind.LETTER
        and d.status != Document.Status.PROCESSING
        and d.created_at >= membership.unread_from
    ]
    if not candidates:
        return set()
    marked = set(
        DocumentReadMark.objects.filter(
            document_id__in=candidates, user_id=membership.user_id
        ).values_list("document_id", flat=True)
    )
    return {d for d in candidates if d not in marked}


def is_unread_for_actor(document: Document) -> bool:
    """Whether the letter is unread for the current user (the letter's detail). False for
    the system and for anybody who is not a member."""
    actor = current_actor()
    if not isinstance(actor, UUID):
        return False
    membership = Membership.objects.filter(
        workspace_id=document.workspace_id, user_id=actor
    ).first()
    if membership is None:
        return False
    return document.id in unread_ids(membership, [document])


def _insert(user_id: UUID, workspace_id: UUID, document_ids: Sequence[UUID]) -> int:
    """Marks for `user_id` on `document_ids` that it lacks, as the current actor (the
    own-row policy checks that it is that user). The number newly written."""
    if not document_ids:
        return 0
    known = set(
        DocumentReadMark.objects.filter(
            document_id__in=list(document_ids), user_id=user_id
        ).values_list("document_id", flat=True)
    )
    now = timezone.now()
    new = [
        DocumentReadMark(workspace_id=workspace_id, document_id=d, user_id=user_id, read_at=now)
        for d in dict.fromkeys(document_ids)
        if d not in known
    ]
    # ON CONFLICT DO NOTHING: a second tab marking the same letter at once changes nothing.
    DocumentReadMark.objects.bulk_create(new, ignore_conflicts=True)
    return len(new)


def mark_read(membership: Membership, document_id: UUID) -> None:
    """The member opened the letter (S 5.3): their own mark, once. 404 like `getDocument`
    for a letter they may not see or that is in the trash. Writes nothing else of the
    member: no history, no audit row.

    Only an opening of a confirmed letter by the member themselves, not only through
    emergency access, is an opening (`opened_at`); only that can make the letter "opened
    by an addressee" (security review of PR #44, points 1 and 3)."""
    # Imported here: `services` builds the letter's answer with this module.
    from apps.documents.services import get_document

    with transaction.atomic():
        document = get_document(membership=membership, document_id=document_id)
        _insert(membership.user_id, membership.workspace_id, [document.id])
        if document.status != Document.Status.DONE or visibility.only_through_emergency(document):
            return
        DocumentReadMark.objects.filter(
            document_id=document.id, user_id=membership.user_id, opened_at__isnull=True
        ).update(opened_at=timezone.now())
        _note_addressee_opened(document, membership)


def mark_unread(membership: Membership, document_id: UUID) -> None:
    """ "Markeer als ongelezen": the member's own mark goes. Whether an addressee opened the
    letter stays (an opening happened). 404 like `getDocument`."""
    from apps.documents.services import get_document

    with transaction.atomic():
        document = get_document(membership=membership, document_id=document_id)
        DocumentReadMark.objects.filter(
            document_id=document.id, user_id=membership.user_id
        ).delete()


def mark_many_read(membership: Membership, document_ids: Sequence[UUID]) -> int:
    """ "Alles gelezen": the member's marks on the given letters, at most 200 (422
    `validation_error`). Ids the member does not see are skipped without a word, so the
    answer says nothing about them. The number newly marked."""
    ids = list(dict.fromkeys(document_ids))
    if len(ids) > MARK_MANY_MAX:
        raise ApiError(422, "validation_error", _("Kies hoogstens 200 brieven."))
    if not ids:
        return 0
    with transaction.atomic():
        documents = list(
            visibility.visible_documents(membership).filter(id__in=ids).only("id", "workspace_id")
        )
        # Read, not opened: "Alles gelezen" is no addressee's opening of any of them.
        return _insert(membership.user_id, membership.workspace_id, [d.id for d in documents])


def mark_scanned(document: Document, user: User) -> None:
    """The uploader made the letter: it is nothing new to them (S 5.3). In the upload's
    transaction, as the uploader."""
    _insert(user.id, document.workspace_id, [document.id])


def my_post_ids(user_id: UUID) -> frozenset[UUID]:
    """The persons (own and managed) and departments whose post is the user's: the
    policy's own function (`brievie_my_post_ids`), so there is no second definition."""
    with connection.cursor() as c:
        c.execute("SELECT public.brievie_my_post_ids(%s)", [user_id])
        row = c.fetchone()
    return frozenset(row[0]) if row is not None and row[0] else frozenset()


def _note_addressee_opened(document: Document, membership: Membership) -> None:
    """The member opened the confirmed letter (`mark_read`); when it is addressed to them
    (their person, a person they manage, their department) it counts as opened by an
    addressee (S 5.5), set once, as the system. It says nothing about who. Never for an
    own document: it is not post that waits to be opened (archive spec S 8)."""
    if document.kind != Document.Kind.LETTER:
        return
    if document.addressee_opened_at is not None or not document.addressee_ids:
        return
    if not my_post_ids(membership.user_id) & set(document.addressee_ids):
        return
    now = timezone.now()
    with system_actor():
        Document.objects.filter(id=document.id, addressee_opened_at__isnull=True).update(
            addressee_opened_at=now
        )
    document.addressee_opened_at = now


def recompute_addressee_opened(document: Document) -> None:
    """After the letter's addressees changed (`visibility.addressee_scope`) or it was
    confirmed (`review`): opened when one of the members it is now addressed to opened
    the confirmed letter (`opened_at`, the earliest), else not. A scan, "Alles gelezen",
    an opening during review or through emergency access never counts. As the system;
    `document.addressee_ids` is the synced list. An own document is never opened by an
    addressee: nothing to recompute (archive spec S 8)."""
    if document.kind != Document.Kind.LETTER:
        return
    with system_actor():
        opened = None
        ids = list(document.addressee_ids)
        if ids:
            persons = Person.objects.filter(id__in=ids, kind=Person.Kind.PERSON)
            users = set(persons.filter(user__isnull=False).values_list("user_id", flat=True))
            users |= {
                manager
                for manager in persons.filter(user__isnull=True).values_list(
                    "managed_by_id", flat=True
                )
                if manager is not None
            }
            if Workspace.objects.filter(
                id=document.workspace_id, departments_enabled=True
            ).exists():
                users |= set(
                    DepartmentMember.objects.filter(department_id__in=ids).values_list(
                        "user_id", flat=True
                    )
                )
            if users:
                opened = DocumentReadMark.objects.filter(
                    document_id=document.id, user_id__in=users, opened_at__isnull=False
                ).aggregate(first=Min("opened_at"))["first"]
        Document.objects.filter(id=document.id).update(addressee_opened_at=opened)
    document.addressee_opened_at = opened
