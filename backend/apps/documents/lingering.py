""" "Blijft liggen": letters nobody picked up, for the sorters of an organisation
(navigation spec S 6).

Two groups: letters too long in the Inbox (to check, or without a name), and confirmed
letters with addressees that none of them opened. Only in an organisation with the
switch on, only for its sorters and admins, and only letters the viewer may see
(`visible_documents`): a letter they may not read is in no list and no count (S 6.2, N8).

The rule is pure (`age_days`, `applies`, `cutoff`); `lingering_letters` and
`count_lingering` apply it through the seam. Days are calendar days in the Netherlands.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.documents import visibility
from apps.documents.models import Document
from apps.workspaces.models import Membership, Workspace

ZONE: Final = ZoneInfo("Europe/Amsterdam")
# Each group shows the oldest 100.
LIST_MAX: Final = 100

_REVIEW: Final = (Document.Status.NEEDS_REVIEW, Document.Status.FAILED)


def age_days(since: datetime, now: datetime) -> int:
    """Calendar days in the Netherlands between `since` and `now`: a letter of Monday
    23:30 is 1 day old on Tuesday 00:10."""
    return (timezone.localtime(now, ZONE).date() - timezone.localtime(since, ZONE).date()).days


def cutoff(days: int, now: datetime) -> datetime:
    """The first moment a letter is less than `days` days old: what arrived before it is
    `days` or more days old (`age_days`)."""
    first_day = timezone.localtime(now, ZONE).date() - timedelta(days=days - 1)
    return datetime.combine(first_day, time.min, tzinfo=ZONE)


def applies(*, kind: str, enabled: bool, sorts_post: bool, role: str) -> bool:
    """Whether the member sees "Blijft liggen": an organisation with the switch on, and a
    member who sorts post or an admin (S 6.2)."""
    if kind != Workspace.Kind.BUSINESS or not enabled:
        return False
    return role == Membership.Role.ADMIN or sorts_post


def applies_to(membership: Membership) -> bool:
    workspace = membership.workspace
    return applies(
        kind=workspace.kind,
        enabled=workspace.lingering_enabled,
        sorts_post=membership.sorts_post,
        role=membership.role,
    )


@dataclass(frozen=True)
class Lingering:
    too_long_in_inbox: list[Document]
    not_opened: list[Document]
    inbox_days: int
    unopened_days: int


def _inbox_arms(before: datetime) -> tuple[Q, Q]:
    """To check, and without a name; each on its own partial index (as `waiting_letters`)."""
    return (
        Q(status__in=_REVIEW, created_at__lt=before),
        Q(status=Document.Status.DONE, addressee_ids=[], created_at__lt=before),
    )


def _not_opened(before: datetime) -> Q:
    """Confirmed, with addressees, opened by none of them, confirmed before `before` (a
    letter without a confirmation time counts from its arrival). The partial index
    `documents_document_unopened`."""
    confirmed = Q(confirmed_at__lt=before) | Q(confirmed_at__isnull=True, created_at__lt=before)
    return (
        Q(status=Document.Status.DONE, addressee_opened_at__isnull=True)
        & ~Q(addressee_ids=[])
        & confirmed
    )


def _listed(documents: QuerySet[Document]) -> QuerySet[Document]:
    # Imported here: the services build the rows with the same prefetches.
    from apps.documents.services import HEADLINE_PROPOSALS, SUGGESTIONS

    return documents.prefetch_related(
        "addressee_links", "label_links", SUGGESTIONS, HEADLINE_PROPOSALS
    )


def lingering_letters(membership: Membership, *, now: datetime | None = None) -> Lingering:
    """Both groups for the member, oldest first, at most 100 each; empty unless
    `applies_to`."""
    workspace = membership.workspace
    inbox_days = workspace.lingering_inbox_days
    unopened_days = workspace.lingering_unopened_days
    if not applies_to(membership):
        return Lingering([], [], inbox_days, unopened_days)
    moment = now or timezone.now()
    letters = visibility.visible_documents(membership)
    inbox: list[Document] = []
    for arm in _inbox_arms(cutoff(inbox_days, moment)):
        inbox += list(_listed(letters.filter(arm)).order_by("created_at", "id")[:LIST_MAX])
    inbox.sort(key=lambda d: (d.created_at, d.id))
    not_opened = list(
        _listed(letters.filter(_not_opened(cutoff(unopened_days, moment)))).order_by(
            "confirmed_at", "created_at", "id"
        )[:LIST_MAX]
    )
    return Lingering(inbox[:LIST_MAX], not_opened, inbox_days, unopened_days)


def count_lingering(membership: Membership, *, now: datetime | None = None) -> int:
    """How many letters linger for the member (both groups, not capped); 0 unless
    `applies_to`. Three counts, each on a partial index."""
    if not applies_to(membership):
        return 0
    workspace = membership.workspace
    moment = now or timezone.now()
    letters = visibility.visible_documents(membership)
    arms = [
        *_inbox_arms(cutoff(workspace.lingering_inbox_days, moment)),
        _not_opened(cutoff(workspace.lingering_unopened_days, moment)),
    ]
    return sum(letters.filter(arm).count() for arm in arms)


def days_of(document: Document, *, now: datetime | None = None) -> int:
    """How long the letter has lain: since it arrived (Inbox) or was confirmed."""
    moment = now or timezone.now()
    if document.status == Document.Status.DONE and document.addressee_ids:
        return age_days(document.confirmed_at or document.created_at, moment)
    return age_days(document.created_at, moment)
