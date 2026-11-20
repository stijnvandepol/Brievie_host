"""Who reads a letter beyond the switches, and the letter's addressee list (S 4.9.2, 4.9.4).

The one writer of `Document.addressee_ids` and of `DocumentReader` rows. Both are derived:
`addressee_ids` from the letter's `DocumentAddressee` rows, the reader rows from its
privacy, addressees, departments and shares. Every write here runs as the system, after
the caller's own authorisation; the nightly check repairs whatever went astray.

`addressee_scope` makes the sync impossible to forget: `organise` refuses to change a
letter's addressees outside one, and the outermost scope syncs once, at its end, in the
same transaction (P3).

`visible_documents` is the one seam of every member-facing read: the list, the inbox
counts, a letter's detail, pages and history, and search all start from it. The
policies decide what a member sees; the seam adds the member filter so PostgreSQL can use
the indexes, and refuses to run as anyone but the member it is asked for.
"""

import logging
import threading
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.documents.models import Document, DocumentReader, DocumentShare
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import SYSTEM, current_actor, system_actor, workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_sql import viewer_pairs_sql

logger = logging.getLogger(__name__)

_NON_EMERGENCY = (
    DocumentReader.Reason.ADDRESSEE,
    DocumentReader.Reason.DEPARTMENT,
    DocumentReader.Reason.SHARE,
)


class _Scopes(threading.local):
    def __init__(self) -> None:
        self.depth: dict[UUID, int] = {}


_scopes = _Scopes()


@contextmanager
def addressee_scope(document: Document) -> Iterator[None]:
    """The block may change the letter's addressees; the outermost scope then syncs
    `addressee_ids` and the reader rows once, as the system. Re-entrant per letter. A
    block that raises syncs nothing (its transaction rolls back anyway)."""
    depth = _scopes.depth.get(document.id, 0)
    _scopes.depth[document.id] = depth + 1
    try:
        yield
    finally:
        if depth == 0:
            del _scopes.depth[document.id]
        else:
            _scopes.depth[document.id] = depth
    if depth == 0:
        sync_addressee_ids(document)
        recompute(document)


def in_addressee_scope(document_id: UUID) -> bool:
    return _scopes.depth.get(document_id, 0) > 0


def sync_addressee_ids(document: Document) -> None:
    """`addressee_ids` := the letter's addressee links, sorted; as the system."""
    with system_actor(), connection.cursor() as c:
        c.execute(
            "UPDATE documents_document SET addressee_ids = ("
            " SELECT COALESCE(array_agg(person_id ORDER BY person_id), '{}')"
            " FROM documents_documentaddressee WHERE document_id = %s)"
            " WHERE id = %s",
            [document.id, document.id],
        )
        c.execute("SELECT addressee_ids FROM documents_document WHERE id = %s", [document.id])
        row = c.fetchone()
    document.addressee_ids = list(row[0]) if row else []


def _members(workspace_id: UUID, user_ids: Collection[UUID]) -> set[UUID]:
    return set(
        Membership.objects.filter(workspace_id=workspace_id, user_id__in=user_ids).values_list(
            "user_id", flat=True
        )
    )


def desired_readers(
    document: Document, *, extra_user_ids: Collection[UUID] = ()
) -> set[tuple[UUID, str]]:
    """The non-emergency reader rows the letter should have: `share` rows for its shares
    (every letter, R44) and `extra_user_ids`; for a private letter also `addressee` rows
    for the accounts of its persons and `department` rows for the members of its
    departments. Current members only. As the system."""
    with system_actor():
        wanted: set[tuple[UUID, str]] = set()
        shares = set(
            DocumentShare.objects.filter(document=document).values_list("user_id", flat=True)
        )
        for user_id in {*shares, *extra_user_ids}:
            wanted.add((user_id, DocumentReader.Reason.SHARE))
        private = Document.objects.filter(
            id=document.id, visibility=Document.Visibility.RESTRICTED
        ).exists()
        if private:
            on = list(document.addressee_links.values_list("person_id", flat=True))
            for user_id in Person.objects.filter(
                id__in=on, kind=Person.Kind.PERSON, user__isnull=False
            ).values_list("user_id", flat=True):
                if user_id is not None:
                    wanted.add((user_id, DocumentReader.Reason.ADDRESSEE))
            for user_id in DepartmentMember.objects.filter(department_id__in=on).values_list(
                "user_id", flat=True
            ):
                wanted.add((user_id, DocumentReader.Reason.DEPARTMENT))
        members = _members(document.workspace_id, {u for u, _reason in wanted})
        return {(u, reason) for u, reason in wanted if u in members}


def recompute(document: Document) -> int:
    """Make the letter's non-emergency reader rows exactly `desired_readers`; the number
    of rows inserted plus deleted. Never raises because the caller loses sight: the
    caller decides that before (PB ruling c). Takes no row lock."""
    with transaction.atomic(), system_actor():
        wanted = desired_readers(document)
        rows = {
            (r.user_id, r.reason): r.id
            for r in DocumentReader.objects.filter(document=document, reason__in=_NON_EMERGENCY)
        }
        stale = [row_id for key, row_id in rows.items() if key not in wanted]
        if stale:
            DocumentReader.objects.filter(id__in=stale).delete()
        added = [key for key in wanted if key not in rows]
        DocumentReader.objects.bulk_create(
            DocumentReader(
                workspace_id=document.workspace_id,
                document=document,
                user_id=user_id,
                reason=reason,
            )
            for user_id, reason in added
        )
        return len(stale) + len(added)


def recompute_where(
    *, workspace_id: UUID, person_id: UUID | None = None, user_id: UUID | None = None
) -> int:
    """`recompute` for every letter whose readers may depend on that person (an addressee)
    or user (a reader, a share, a linked person, a department membership)."""
    with transaction.atomic(), system_actor():
        documents = Document.objects.filter(workspace_id=workspace_id)
        condition = Q(pk__in=[])
        if person_id is not None:
            condition |= Q(
                visibility=Document.Visibility.RESTRICTED, addressee_ids__contains=[person_id]
            )
        if user_id is not None:
            post_ids = [
                *Person.objects.filter(workspace_id=workspace_id, user_id=user_id).values_list(
                    "id", flat=True
                ),
                *DepartmentMember.objects.filter(
                    workspace_id=workspace_id, user_id=user_id
                ).values_list("department_id", flat=True),
            ]
            condition |= (
                Q(readers__user_id=user_id)
                | Q(shares__user_id=user_id)
                | Q(visibility=Document.Visibility.RESTRICTED, addressee_ids__overlap=post_ids)
            )
        return sum(recompute(d) for d in documents.filter(condition).distinct().order_by("id"))


def can_read(document_id: UUID) -> bool:
    """Whether the current actor sees the letter (the policies decide, no system)."""
    return Document.objects.filter(id=document_id).exists()


def member_filter() -> Q | None:
    """For a user who neither sees all post nor sorts it: a condition the lists add so
    PostgreSQL can use the indexes (S 4.9.5). Otherwise None.

    A superset of what the policy shows such a user, every arm on an index: the
    addressees through the GIN index, the scanner through its foreign key, the reader
    rows (a private letter is seen only through one) through theirs. The policy still
    decides; the filter changes no result. One query, through the policy's own helpers.
    """
    actor = current_actor()
    if not isinstance(actor, UUID):
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.brievie_sees_all_post(%s), public.brievie_actor_sorts_post(%s),"
            " public.brievie_my_post_ids(%s)",
            [actor, actor, actor],
        )
        row = cursor.fetchone()
    if row is None or row[0] or row[1]:
        return None
    my_post_ids: list[UUID] = list(row[2])
    return (
        Q(addressee_ids__overlap=my_post_ids)
        | Q(scanned_by_id=actor)
        | Q(id__in=DocumentReader.objects.filter(user_id=actor).values("document_id"))
    )


def status_of(document_id: UUID) -> str:
    """A letter's status, also when the caller no longer sees it (the answer of a
    hand-over, `can_read` false: the id and status only). As the system."""
    with system_actor():
        status = Document.objects.filter(id=document_id).values_list("status", flat=True).first()
    return status or ""


def reader_names(documents: Sequence[Document]) -> dict[UUID, list[str]]:
    """The names of the readers of the private letters among `documents` the current
    actor may read, sorted without regard to case."""
    readable = set(
        Document.objects.filter(
            id__in=[d.id for d in documents], visibility=Document.Visibility.RESTRICTED
        ).values_list("id", flat=True)
    )
    if not readable:
        return {}
    now = timezone.now()
    with system_actor():
        rows = list(
            DocumentReader.objects.filter(document_id__in=readable, reason__in=_NON_EMERGENCY)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .values_list("document_id", "user__name")
            .distinct()
        )
    names: dict[UUID, set[str]] = {}
    for document_id, name in rows:
        names.setdefault(document_id, set()).add(name)
    return {d: sorted(n, key=str.casefold) for d, n in names.items()}


def viewers(document_ids: Collection[UUID]) -> dict[UUID, frozenset[UUID]]:
    """Every current member who sees each letter, by the policy's own condition run for
    each member (`visibility_sql.viewer_pairs_sql`, reader rows included), as the system."""
    ids = list(dict.fromkeys(document_ids))
    found: dict[UUID, set[UUID]] = {d: set() for d in ids}
    if not ids:
        return {}
    with system_actor(), connection.cursor() as c:
        c.execute(viewer_pairs_sql("d.id = ANY(%s::uuid[])"), [ids])
        for document_id, user_id in c.fetchall():
            found[document_id].add(user_id)
    return {d: frozenset(users) for d, users in found.items()}


@dataclass(frozen=True)
class VisibilityCheck:
    addressee_ids_fixed: int
    reader_rows_fixed: int


_REPAIR_ADDRESSEE_IDS = (
    "UPDATE documents_document d SET addressee_ids = s.ids"
    " FROM (SELECT d2.id, COALESCE(array_agg(a.person_id ORDER BY a.person_id)"
    " FILTER (WHERE a.person_id IS NOT NULL), '{}') AS ids"
    " FROM documents_document d2"
    " LEFT JOIN documents_documentaddressee a ON a.document_id = d2.id"
    " GROUP BY d2.id) s"
    " WHERE s.id = d.id AND d.addressee_ids IS DISTINCT FROM s.ids"
)


def verify_visibility(workspace_id: UUID) -> VisibilityCheck:
    """Repair the workspace's derived visibility, as the system: every `addressee_ids`
    that differs from the links, expired emergency rows and shares of former members
    removed, and the reader rows of every letter that has any. Counts only."""
    with transaction.atomic(), system_actor():
        with connection.cursor() as c:
            c.execute(_REPAIR_ADDRESSEE_IDS)
            fixed_ids = c.rowcount
        DocumentReader.objects.filter(
            reason=DocumentReader.Reason.EMERGENCY, expires_at__lte=timezone.now()
        ).delete()
        members = Membership.objects.filter(workspace_id=workspace_id).values("user_id")
        DocumentShare.objects.exclude(user_id__in=members).delete()
        documents = (
            Document.objects.filter(
                Q(visibility=Document.Visibility.RESTRICTED)
                | Q(shares__isnull=False)
                | Q(readers__reason__in=_NON_EMERGENCY)
            )
            .distinct()
            .order_by("id")
        )
        fixed_readers = sum(recompute(d) for d in documents)
    return VisibilityCheck(addressee_ids_fixed=fixed_ids, reader_rows_fixed=fixed_readers)


def verify_all() -> VisibilityCheck:
    """`verify_visibility` for every workspace, each in its own short transaction."""
    fixed_ids = fixed_readers = 0
    for workspace_id in Workspace.objects.order_by("id").values_list("id", flat=True):
        with workspace_context(workspace_id, actor=SYSTEM):
            check = verify_visibility(workspace_id)
        fixed_ids += check.addressee_ids_fixed
        fixed_readers += check.reader_rows_fixed
    return VisibilityCheck(addressee_ids_fixed=fixed_ids, reader_rows_fixed=fixed_readers)


def visible_documents(membership: Membership) -> QuerySet[Document]:
    """The letters `membership`'s user may see in its workspace: the seam of every
    member-facing read (list, inbox counts, detail, pages, history, search).

    The document policies decide (S 4.1), with the request's actor; the member filter
    (S 4.9.5) only lets PostgreSQL use the indexes and changes no result. Runs only as
    that member: as the system or another user the policies would show other letters,
    so that is a programming error (fail closed). The generated search vector is not
    loaded: no reader needs it, and it is about as large as the text.
    """
    if current_actor() != membership.user_id:
        raise RuntimeError("visible_documents() runs only as the member it is asked for.")
    documents = Document.objects.filter(workspace_id=membership.workspace_id).defer("search_vector")
    member = member_filter()
    return documents if member is None else documents.filter(member)
