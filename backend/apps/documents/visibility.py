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
the indexes, leaves out the letters in the trash, and refuses to run as anyone but the
member it is asked for. `trashed_documents` is its sibling for the trash, on the same
rights.
"""

import logging
import threading
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents.models import LIVE, Document, DocumentEvent, DocumentReader, DocumentShare
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import SYSTEM, current_actor, system_actor, workspace_context
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_sql import viewer_pairs_sql

if TYPE_CHECKING:
    from apps.rules.models import Rule

logger = logging.getLogger(__name__)

_NON_EMERGENCY = (
    DocumentReader.Reason.ADDRESSEE,
    DocumentReader.Reason.DEPARTMENT,
    DocumentReader.Reason.SHARE,
    DocumentReader.Reason.MANAGED,
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
        if sync_addressee_ids(document):
            # Imported here: `reading` reads letters through this module.
            from apps.documents.reading import recompute_addressee_opened

            recompute_addressee_opened(document)
        recompute(document)
        _release_assignees(document.workspace_id, [document.id])


def _release_assignees(workspace_id: UUID, document_ids: Collection[UUID] | None) -> int:
    """An assignment ends when the assignee may no longer read the letter (Mijn taken
    S 5.3): after every change of who reads it. Imported here: the tasks app reads
    letters through this module."""
    from apps.tasks.assignment import release_unseeing_assignees

    return release_unseeing_assignees(workspace_id=workspace_id, document_ids=document_ids)


def in_addressee_scope(document_id: UUID) -> bool:
    return _scopes.depth.get(document_id, 0) > 0


def sync_addressee_ids(document: Document) -> bool:
    """`addressee_ids` := the letter's addressee links, sorted; as the system. Whether the
    list changed."""
    with system_actor(), connection.cursor() as c:
        c.execute("SELECT addressee_ids FROM documents_document WHERE id = %s", [document.id])
        before = c.fetchone()
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
    return (list(before[0]) if before else []) != document.addressee_ids


def _members(workspace_id: UUID, user_ids: Collection[UUID]) -> set[UUID]:
    return set(
        Membership.objects.filter(workspace_id=workspace_id, user_id__in=user_ids).values_list(
            "user_id", flat=True
        )
    )


def desired_readers(
    document: Document, *, extra_user_ids: Collection[UUID] = (), private: bool | None = None
) -> set[tuple[UUID, str]]:
    """The non-emergency reader rows the letter should have: `share` rows for its shares
    (every letter, R44) and `extra_user_ids`; for a private letter also `addressee` rows
    for the accounts of its persons and `department` rows for the members of its
    departments. Current members only. As the system. `private` None reads the letter's
    stored visibility; `make_private` passes True before the flip."""
    with system_actor():
        wanted: set[tuple[UUID, str]] = set()
        shares = set(
            DocumentShare.objects.filter(document=document).values_list("user_id", flat=True)
        )
        for user_id in {*shares, *extra_user_ids}:
            wanted.add((user_id, DocumentReader.Reason.SHARE))
        if private is None:
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
            # "Beheerd door": the manager of a person without an account reads their post.
            for manager_id in Person.objects.filter(
                id__in=on, kind=Person.Kind.PERSON, user__isnull=True, managed_by__isnull=False
            ).values_list("managed_by_id", flat=True):
                if manager_id is not None:
                    wanted.add((manager_id, DocumentReader.Reason.MANAGED))
            for user_id in DepartmentMember.objects.filter(department_id__in=on).values_list(
                "user_id", flat=True
            ):
                wanted.add((user_id, DocumentReader.Reason.DEPARTMENT))
        members = _members(document.workspace_id, {u for u, _reason in wanted})
        return {(u, reason) for u, reason in wanted if u in members}


def recompute(document: Document, *, private: bool | None = None) -> int:
    """Make the letter's non-emergency reader rows exactly `desired_readers`; the number
    of rows inserted plus deleted. Never raises because the caller loses sight: the
    caller decides that before (PB ruling c). Takes no row lock."""
    with transaction.atomic(), system_actor():
        wanted = desired_readers(document, private=private)
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
                *Person.objects.filter(
                    workspace_id=workspace_id, managed_by_id=user_id, user__isnull=True
                ).values_list("id", flat=True),
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


def visibility_of(document_id: UUID) -> str:
    """A letter's visibility, also when the caller no longer sees it; as the system."""
    with system_actor():
        value = Document.objects.filter(id=document_id).values_list("visibility", flat=True).first()
    return value or Document.Visibility.WORKSPACE


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


# Emergency access ends at a time of day in the Netherlands, whatever the server's zone.
_REASON_ZONE = ZoneInfo("Europe/Amsterdam")


def viewer_reasons(document: Document) -> dict[UUID, list[str]]:
    """Who sees the letter and why ("Wie kan deze brief zien?", S 4.15): every viewer of
    `viewers()`, each with the arms of S 4.1 that hold for them, read as the system. A
    private letter's viewers are its readers: as addressee ("op naam van"), department
    member, share or emergency access."""
    from apps.workspaces.visibility_rules import ADMIN
    from apps.workspaces.visibility_settings import snapshot

    seen = viewers([document.id]).get(document.id, frozenset())
    policy = snapshot(Workspace.objects.get(id=document.workspace_id))
    now = timezone.now()
    with system_actor():
        letter = Document.objects.get(id=document.id)
        rows = [
            (user_id, reason, expires_at)
            for user_id, reason, expires_at in DocumentReader.objects.filter(
                document_id=document.id
            ).values_list("user_id", "reason", "expires_at")
            if expires_at is None or expires_at > now
        ]
    on = set(letter.addressee_ids)
    private = letter.visibility == Document.Visibility.RESTRICTED
    switches = policy.switches
    unread = not on & policy.covered_ids
    found: dict[UUID, list[str]] = {}
    for member in policy.members:
        if member.user_id not in seen:
            continue
        reasons: list[str] = []
        readers = {reason: expires for user, reason, expires in rows if user == member.user_id}
        persons = sorted(
            policy.person_names[p] for p in on & (member.person_ids - member.managed_ids)
        )
        managed = sorted(policy.person_names[p] for p in on & member.managed_ids)
        departments = sorted(policy.department_names[d] for d in on & member.department_ids)
        if not private:
            if switches.everyone_sees_all:
                reasons.append(_("ziet alle post"))
            elif switches.admins_read_all and member.role == ADMIN:
                reasons.append(_("beheerder, leest alle post"))
        if not private or DocumentReader.Reason.ADDRESSEE in readers:
            reasons += [_("op naam van %(person)s") % {"person": p} for p in persons]
        if not private or DocumentReader.Reason.MANAGED in readers:
            reasons += [_("beheert de post van %(person)s") % {"person": p} for p in managed]
        if (not private and switches.departments_enabled) or (
            DocumentReader.Reason.DEPARTMENT in readers
        ):
            reasons += [_("afdeling %(department)s") % {"department": d} for d in departments]
        if not private and unread and member.user_id in policy.sorter_ids:
            reasons.append(_("verdeelt post"))
        if (
            not private
            and letter.scanned_by_id == member.user_id
            and letter.status != Document.Status.DONE
        ):
            reasons.append(_("heeft de brief gescand"))
        if DocumentReader.Reason.SHARE in readers:
            reasons.append(_("is gedeeld"))
        expires = readers.get(DocumentReader.Reason.EMERGENCY)
        if expires is not None:
            until = timezone.localtime(expires, _REASON_ZONE).strftime("%H:%M")
            reasons.append(_("noodtoegang tot %(time)s") % {"time": until})
        found[member.user_id] = reasons
    return found


MakePrivateResult = Literal["made_private", "already_private", "nobody_could_read"]


def _member_not_found() -> ApiError:
    return ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))


def _history(
    document: Document,
    kind: str,
    *,
    actor_id: UUID | None,
    origin: str,
    rule: "Rule | None" = None,
    person: Person | None = None,
    person_name: str = "",
    note: str = "",
) -> None:
    """A line in the letter's history about who reads it (no label or addressee change:
    those are `organise`'s)."""
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=kind,
        origin=origin,
        actor_id=actor_id,
        rule=rule,
        rule_name=rule.name if rule is not None else "",
        person=person,
        person_name=person.display_name if person is not None else person_name,
        note=note,
    )


def _is_private(document: Document) -> bool:
    with system_actor():
        return Document.objects.filter(
            id=document.id, visibility=Document.Visibility.RESTRICTED
        ).exists()


def _audit_visibility(document: Document, actor: AuditActor, values: dict[str, str]) -> None:
    """`visibility_changed` on the letter by its id only (never a sender or text)."""
    record(
        AuditCode.VISIBILITY_CHANGED,
        actor=actor,
        target=AuditTarget(TargetKind.DOCUMENT, document.id),
        workspace_id=document.workspace_id,
        values=values,
    )


def make_private(
    document: Document,
    *,
    actor: AuditActor,
    origin: str,
    rule: "Rule | None" = None,
    share_user_ids: Collection[UUID] = (),
) -> MakePrivateResult:
    """Make the letter private (PB T7, S 4.8). The caller holds the letter's lock (and in a
    request its own membership first).

    The readers are the scanner (while a member), the current user, `share_user_ids` (each
    a member, else 404 `member_not_found`) as shares, and the accounts of its persons and
    the members of its departments. Nobody among them → nothing changes: a
    `private_skipped` line, and the admins get one notice per letter (ruling d). Order:
    the history line, the shares and readers as the system, then the flip **as the
    current actor**, so the database proves the actor kept access (PB decision 6)."""
    with transaction.atomic():
        if _is_private(document):
            document.visibility = Document.Visibility.RESTRICTED
            return "already_private"
        wanted = set(share_user_ids)
        if wanted - _members(document.workspace_id, wanted):
            raise _member_not_found()
        extra = set(wanted)
        if document.scanned_by_id is not None:
            extra.add(document.scanned_by_id)
        current = current_actor()
        if isinstance(current, UUID):
            extra.add(current)
        extra &= _members(document.workspace_id, extra)
        if not desired_readers(document, extra_user_ids=extra, private=True):
            _private_skipped(document, actor=actor, origin=origin, rule=rule)
            return "nobody_could_read"
        _history(
            document,
            DocumentEvent.Kind.MADE_PRIVATE,
            actor_id=actor.user_id,
            origin=origin,
            rule=rule,
        )
        with system_actor():
            known = set(
                DocumentShare.objects.filter(document=document).values_list("user_id", flat=True)
            )
            DocumentShare.objects.bulk_create(
                DocumentShare(
                    workspace_id=document.workspace_id,
                    document=document,
                    user_id=user_id,
                    created_by_id=actor.user_id,
                )
                for user_id in sorted(extra - known)
            )
            recompute(document, private=True)
        # As the current actor: 42501 when they would not read it afterwards.
        flipped = Document.objects.filter(id=document.id).update(
            visibility=Document.Visibility.RESTRICTED, updated_at=timezone.now()
        )
        if flipped != 1:
            raise RuntimeError("make_private ran as an actor who does not see the letter.")
        document.visibility = Document.Visibility.RESTRICTED
        _release_assignees(document.workspace_id, [document.id])
        _audit_visibility(
            document, actor, {"visibility": Document.Visibility.RESTRICTED, "origin": origin}
        )
        return "made_private"


def _private_skipped(
    document: Document, *, actor: AuditActor, origin: str, rule: "Rule | None"
) -> None:
    """Ruling d: the letter stays visible; the history says so and every admin hears it
    once per letter. The line's kind says why: no stored sentence (it would not follow
    the reader's language)."""
    from apps.accounts.notices import notify  # accounts imports this module's callers

    with system_actor():
        told = DocumentEvent.objects.filter(
            document=document, kind=DocumentEvent.Kind.PRIVATE_SKIPPED
        ).exists()
        _history(
            document,
            DocumentEvent.Kind.PRIVATE_SKIPPED,
            actor_id=actor.user_id,
            origin=origin,
            rule=rule,
        )
    if told:
        return
    workspace = Workspace.objects.get(id=document.workspace_id)
    admins = list(
        Membership.objects.filter(
            workspace_id=document.workspace_id, role=Membership.Role.ADMIN
        ).select_related("user")
    )
    # The letter stays visible by the switches: only an admin who sees it learns which
    # letter it is. When none does, every admin hears that it happened, without the id
    # (the letter's readers see the history line on it).
    seen = regular_viewers([document.id]).get(document.id, frozenset())
    named = [m for m in admins if m.user_id in seen]
    for membership in named:
        notify(
            membership.user,
            "private_without_readers",
            actor_name="Brievie",
            values={"document_id": str(document.id), "workspace": workspace.name},
        )
    if not named:
        for membership in admins:
            notify(
                membership.user,
                "private_without_readers_unnamed",
                actor_name="Brievie",
                values={"workspace": workspace.name},
            )


def can_make_public(document: Document, user_id: UUID) -> bool:
    """A reader through an addressee or department row may; when the letter has no such
    rows at all (no addressee with an account), any reader may. As the system."""
    with system_actor():
        rows = DocumentReader.objects.filter(document=document).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        )
        own = set(
            rows.filter(user_id=user_id)
            .exclude(reason=DocumentReader.Reason.EMERGENCY)
            .values_list("reason", flat=True)
        )
        if own & {DocumentReader.Reason.ADDRESSEE, DocumentReader.Reason.DEPARTMENT}:
            return True
        addressed = rows.filter(
            reason__in=(DocumentReader.Reason.ADDRESSEE, DocumentReader.Reason.DEPARTMENT)
        ).exists()
        return bool(own) and not addressed


def make_public(document: Document, *, actor: AuditActor, user_id: UUID) -> bool:
    """Make a private letter visible by the switches again (PB T7); False when it was not
    private. 403 `cannot_make_public` for someone not addressed while an addressee reads
    it. Its shares go (switched private again it starts from scanner and actor, PB
    decision 9). The flip runs as the current actor while their reader row still exists,
    then the readers follow as the system."""
    with transaction.atomic():
        if not _is_private(document):
            return False
        if not can_make_public(document, user_id):
            raise ApiError(
                403,
                "cannot_make_public",
                _(
                    "Alleen iemand op wiens naam deze brief staat, kan hem weer voor "
                    "iedereen zichtbaar maken."
                ),
            )
        _history(document, DocumentEvent.Kind.MADE_PUBLIC, actor_id=actor.user_id, origin="user")
        Document.objects.filter(id=document.id).update(
            visibility=Document.Visibility.WORKSPACE, updated_at=timezone.now()
        )
        document.visibility = Document.Visibility.WORKSPACE
        with system_actor():
            DocumentShare.objects.filter(document=document).delete()
            DocumentReader.objects.filter(
                document=document, reason=DocumentReader.Reason.EMERGENCY
            ).delete()
            recompute(document, private=False)
        _release_assignees(document.workspace_id, [document.id])
        _audit_visibility(
            document, actor, {"visibility": Document.Visibility.WORKSPACE, "origin": "user"}
        )
        return True


def shared_user_ids(document: Document) -> list[UUID]:
    """The members the letter is shared with, as the system."""
    with system_actor():
        return list(
            DocumentShare.objects.filter(document=document)
            .order_by("user_id")
            .values_list("user_id", flat=True)
        )


def set_shares(document: Document, *, user_ids: Collection[UUID], actor: AuditActor) -> None:
    """The letter's shares become exactly `user_ids` ("Delen met"), on any letter (R44).

    Each a current member (404 `member_not_found`). A history line and an audit row per
    added or removed share; then the readers follow. Removing one's own share is allowed:
    the caller may lose the letter (the endpoint answers `can_read` false)."""
    wanted = set(user_ids)
    with transaction.atomic():
        if wanted - _members(document.workspace_id, wanted):
            raise _member_not_found()
        private = _is_private(document)
        shown = Document.Visibility.RESTRICTED if private else Document.Visibility.WORKSPACE
        with system_actor():
            current = set(
                DocumentShare.objects.filter(document=document).values_list("user_id", flat=True)
            )
            names = dict(User.objects.filter(id__in=wanted | current).values_list("id", "name"))
            added = sorted(wanted - current)
            removed = sorted(current - wanted)
            for user_id in added:
                DocumentShare.objects.create(
                    workspace_id=document.workspace_id,
                    document=document,
                    user_id=user_id,
                    created_by_id=actor.user_id,
                )
            DocumentShare.objects.filter(document=document, user_id__in=removed).delete()
            for kind, ids in (
                (DocumentEvent.Kind.SHARE_ADDED, added),
                (DocumentEvent.Kind.SHARE_REMOVED, removed),
            ):
                for user_id in ids:
                    _history(
                        document,
                        kind,
                        actor_id=actor.user_id,
                        origin="user",
                        person_name=names.get(user_id, ""),
                    )
            recompute(document)
        _release_assignees(document.workspace_id, [document.id])
        for share, ids in (("added", added), ("removed", removed)):
            for user_id in ids:
                _audit_visibility(
                    document,
                    actor,
                    {"visibility": shown, "share": share, "member": names.get(user_id, "")},
                )


@contextmanager
def keep_own_access(document: Document) -> Iterator[None]:
    """409 `would_lose_access` when the current user could read the private letter before
    the block and cannot after it (PB ruling c): around an organising change of its
    addressees, outside its `addressee_scope` so the readers are recomputed first. Only
    for a private letter (S 4.9.3): handing over a letter that is not private is allowed
    (`can_read` false). A no-op for the system and the empty actor."""
    actor = current_actor()
    before = isinstance(actor, UUID) and can_read(document.id)
    yield
    if before and _is_private(document) and not can_read(document.id):
        raise ApiError(409, "would_lose_access", _("Dan kun je deze brief zelf niet meer lezen."))


def has_private_post(person: Person) -> bool:
    """Whether a private letter is on this person's (or department's) name; as the system."""
    with system_actor():
        return Document.objects.filter(
            workspace_id=person.workspace_id,
            visibility=Document.Visibility.RESTRICTED,
            addressee_ids__contains=[person.id],
        ).exists()


def _private_reader_rows() -> QuerySet[DocumentReader]:
    """Non-emergency, unexpired reader rows on private letters (the current workspace)."""
    return DocumentReader.objects.filter(
        reason__in=_NON_EMERGENCY, document__visibility=Document.Visibility.RESTRICTED
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


def reads_private_post(user_id: UUID) -> bool:
    """Whether the user reads a private letter of the current workspace (not by emergency
    access); as the system."""
    with system_actor():
        return _private_reader_rows().filter(user_id=user_id).exists()


def private_post_readers() -> set[UUID]:
    """The users who read a private letter of the current workspace; one query."""
    with system_actor():
        return set(_private_reader_rows().values_list("user_id", flat=True).distinct())


def forget_member(workspace_id: UUID, user_id: UUID) -> None:
    """A member leaves: their reader rows, shares and department memberships in this
    workspace go, as the system. Nothing else changes: the readers of every letter are
    then the old ones without this user."""
    with transaction.atomic(), system_actor():
        DocumentReader.objects.filter(workspace_id=workspace_id, user_id=user_id).delete()
        DocumentShare.objects.filter(workspace_id=workspace_id, user_id=user_id).delete()
        DepartmentMember.objects.filter(workspace_id=workspace_id, user_id=user_id).delete()


def record_grant(person: Person, *, grantee: User, actor_user_id: UUID | None) -> int:
    """Someone was given sight of the person's private post (linked, or added to the
    department): an `access_granted` line on each of its private letters, which its
    readers see. The number of letters."""
    with transaction.atomic(), system_actor():
        documents = list(
            Document.objects.filter(
                workspace_id=person.workspace_id,
                visibility=Document.Visibility.RESTRICTED,
                addressee_ids__contains=[person.id],
            ).order_by("id")
        )
        for document in documents:
            _history(
                document,
                DocumentEvent.Kind.ACCESS_GRANTED,
                actor_id=actor_user_id,
                origin="user",
                person=person,
                note=grantee.name,
            )
        return len(documents)


EMERGENCY_HOURS = 1
REASON_MIN = 10
# The audit log cuts strings at 200: the whole reason stays readable there.
REASON_MAX = 200


@dataclass(frozen=True)
class EmergencyResult:
    opened_count: int
    expires_at: datetime


def open_emergency(
    *, workspace: Workspace, person: Person, actor: AuditActor, reason: str
) -> EmergencyResult:
    """Emergency access (S 4.7, D 8.5, PB T20): the admin reads, for one hour, every letter
    on the person's or department's name they cannot see now, private or not.

    409 `emergency_access_off` before anything else when the workspace has it off; the
    reason, BSN-masked and trimmed, is 10 to 200 characters (422). As the system: an
    `emergency` reader row per letter (renewed when there is one), a history line with the
    reason on each, one audit row; the person's account or the department's members get
    a notice when something opened. Read-only: see `require_editable`."""
    from apps.accounts.notices import notify  # accounts imports this module's callers

    if not Workspace.objects.filter(id=workspace.id, emergency_access=True).exists():
        raise ApiError(409, "emergency_access_off", _("Noodtoegang staat uit in deze werkruimte."))
    reason = mask_bsns(reason).strip()
    if not REASON_MIN <= len(reason) <= REASON_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Geef een reden van %(min)d tot %(max)d tekens.")
            % {"min": REASON_MIN, "max": REASON_MAX},
        )
    if actor.user_id is None:
        raise RuntimeError("Emergency access is opened by a member.")
    admin_id = actor.user_id
    expires_at = timezone.now() + timedelta(hours=EMERGENCY_HOURS)
    with transaction.atomic():
        lock_own_membership(workspace.id, admin_id)
        with system_actor():
            on_name = list(
                Document.objects.filter(
                    workspace_id=workspace.id, addressee_ids__contains=[person.id]
                )
                .filter(LIVE)
                .order_by("id")
            )
            # An hour already running is renewed: only regular sight counts as seeing.
            seen = regular_viewers([d.id for d in on_name])
            opened = [d for d in on_name if admin_id not in seen.get(d.id, frozenset())]
            for document in opened:
                DocumentReader.objects.update_or_create(
                    document=document,
                    user_id=admin_id,
                    reason=DocumentReader.Reason.EMERGENCY,
                    defaults={"workspace_id": workspace.id, "expires_at": expires_at},
                )
                _history(
                    document,
                    DocumentEvent.Kind.EMERGENCY_OPENED,
                    actor_id=admin_id,
                    origin="user",
                    note=reason,
                )
        record(
            AuditCode.EMERGENCY_ACCESS_USED,
            actor=actor,
            target=AuditTarget(TargetKind.PERSON, person.id, person.display_name),
            workspace_id=workspace.id,
            values={"reason": reason, "document_count": len(opened)},
        )
        if opened:
            concerned = (
                [person.user_id]
                if person.user_id is not None
                else list(
                    DepartmentMember.objects.filter(department=person).values_list(
                        "user_id", flat=True
                    )
                )
            )
            for user in User.objects.filter(id__in=concerned):
                notify(
                    user,
                    "emergency_access_used",
                    actor_name=actor.name,
                    values={"person": person.display_name, "count": str(len(opened))},
                )
    return EmergencyResult(opened_count=len(opened), expires_at=expires_at)


def end_emergency_access(workspace_id: UUID) -> int:
    """Emergency access was switched off: every running hour ends now, in the caller's
    transaction, and an assignment that rested on it goes. The number of rows removed."""
    with workspace_context(workspace_id, actor=SYSTEM), transaction.atomic():
        removed, _rows = DocumentReader.objects.filter(
            workspace_id=workspace_id, reason=DocumentReader.Reason.EMERGENCY
        ).delete()
        if removed:
            _release_assignees(workspace_id, None)
        return removed


def _sees_without_emergency(document: Document, user_id: UUID) -> bool:
    """Whether the user would see the letter without their emergency rows: the rows are
    taken away inside a savepoint that is rolled back, and the policy's own condition
    asked (`viewers`)."""
    with transaction.atomic(), system_actor():
        DocumentReader.objects.filter(
            document=document, user_id=user_id, reason=DocumentReader.Reason.EMERGENCY
        ).delete()
        seen = user_id in viewers([document.id]).get(document.id, frozenset())
        transaction.set_rollback(True)
    return seen


def _has_emergency(document: Document, user_id: UUID) -> bool:
    with system_actor():
        return DocumentReader.objects.filter(
            document=document,
            user_id=user_id,
            reason=DocumentReader.Reason.EMERGENCY,
            expires_at__gt=timezone.now(),
        ).exists()


def can_edit(document: Document, user: User) -> bool:
    """S 4.9.3: a member or admin who sees the letter, and not only through emergency
    access (read-only, PB decision 10)."""
    membership = Membership.objects.filter(
        workspace_id=document.workspace_id, user_id=user.id
    ).first()
    if membership is None or membership.role == Membership.Role.VIEWER:
        return False
    if not can_read(document.id):
        return False
    return not _has_emergency(document, user.id) or _sees_without_emergency(document, user.id)


def only_through_emergency(document: Document) -> bool:
    """Whether the current user sees the letter only through emergency access (the system
    never does). The caller found the letter as the user already."""
    actor = current_actor()
    if not isinstance(actor, UUID) or not _has_emergency(document, actor):
        return False
    return not _sees_without_emergency(document, actor)


def require_editable(document: Document) -> None:
    """403 `emergency_read_only` when the current user sees the letter only through
    emergency access: no organising, sharing, publishing, confirming, retrying or task
    change, so an hour's access can never be kept or the letter sent to the AI again.
    A no-op for the system. The caller found the letter as the user already."""
    if only_through_emergency(document):
        raise ApiError(
            403, "emergency_read_only", _("Met noodtoegang kun je deze brief alleen lezen.")
        )


def regular_viewers(document_ids: Collection[UUID]) -> dict[UUID, frozenset[UUID]]:
    """`viewers` without emergency access: who reads each letter by the switches, links,
    departments, scanning and shares. The emergency rows are taken away inside a savepoint
    that is rolled back, so the condition stays the policy's own. For what lasts beyond an
    emergency hour (an assignment) and for what that hour opens."""
    ids = list(dict.fromkeys(document_ids))
    if not ids:
        return {}
    with transaction.atomic(), system_actor():
        DocumentReader.objects.filter(
            document_id__in=ids, reason=DocumentReader.Reason.EMERGENCY
        ).delete()
        seen = viewers(ids)
        transaction.set_rollback(True)
    return seen


@dataclass(frozen=True)
class VisibilityCheck:
    addressee_ids_fixed: int
    reader_rows_fixed: int
    # Assignments taken back because the assignee may no longer read the letter.
    assignments_released: int = 0


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
        released = _release_assignees(workspace_id, None)
    return VisibilityCheck(
        addressee_ids_fixed=fixed_ids,
        reader_rows_fixed=fixed_readers,
        assignments_released=released,
    )


def verify_all() -> VisibilityCheck:
    """`verify_visibility` for every workspace, each in its own short transaction."""
    fixed_ids = fixed_readers = released = 0
    for workspace_id in Workspace.objects.order_by("id").values_list("id", flat=True):
        with workspace_context(workspace_id, actor=SYSTEM):
            check = verify_visibility(workspace_id)
        fixed_ids += check.addressee_ids_fixed
        fixed_readers += check.reader_rows_fixed
        released += check.assignments_released
    return VisibilityCheck(
        addressee_ids_fixed=fixed_ids,
        reader_rows_fixed=fixed_readers,
        assignments_released=released,
    )


def _readable(membership: Membership) -> QuerySet[Document]:
    """Every letter `membership`'s user may read in its workspace, in the trash or not.

    The document policies decide (S 4.1), with the request's actor; the member filter
    (S 4.9.5) only lets PostgreSQL use the indexes and changes no result. Runs only as
    that member: as the system or another user the policies would show other letters,
    so that is a programming error (fail closed). The generated search vector is not
    loaded: no reader needs it, and it is about as large as the text.
    """
    if current_actor() != membership.user_id:
        raise RuntimeError("The document seam runs only as the member it is asked for.")
    documents = Document.objects.filter(workspace_id=membership.workspace_id).defer("search_vector")
    member = member_filter()
    return documents if member is None else documents.filter(member)


def visible_documents(membership: Membership) -> QuerySet[Document]:
    """The letters `membership`'s user may see in its workspace, the trash left out.

    Every query that returns or counts letters to a user starts here or at its siblings
    (`trashed_documents` for the trash, `readable_documents` for a reference): the list,
    the inbox counts, a letter's detail, pages and history, search, and the duplicate
    pairs (shown only when both letters are readable). It runs under the caller's actor;
    the policy decides, the member filter only lets PostgreSQL use the indexes. A count
    of hidden matches is never returned. A system-side comparison (duplicate detection)
    runs inside `system_actor()` and answers the user only through these seams or
    `can_read`. `tests/test_visibility_seams.py` fails a member-facing read that does not
    start here."""
    return _readable(membership).filter(LIVE)


def readable_documents(membership: Membership) -> QuerySet[Document]:
    """Every letter `membership`'s user may read, the trash included: only to decide
    whether a reference to another letter (a history line's `related_document`) may be
    shown. Lists and detail start from `visible_documents`."""
    return _readable(membership)


def trashed_documents(membership: Membership) -> QuerySet[Document]:
    """The letters in the trash `membership`'s user may see, on the same rights as
    `visible_documents`: the only read of the trash (list, count, restore, purge-now,
    empty). A letter whose purge started is in neither."""
    return _readable(membership).filter(trashed_at__isnull=False, purge_started_at__isnull=True)
