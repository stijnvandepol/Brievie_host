"""Duplicate letters: two scans of one letter, proposed as a pair (spec
`docs/superpowers/specs/2026-11-20-duplicates-design.md`).

`visible_pairs` is the seam of every member-facing read and change of a pair: the list,
a pair's detail, the counts, merge and dismiss. It stands on `visibility.visible_documents`,
so a pair is shown only to a member who sees both letters, never with a letter in the
trash, and only as that member (the seam raises otherwise). A pair the member cannot see
in full answers as a missing one (`duplicate_not_found`).

This module never switches to the system: detection runs as the system only because its
callers (the pipeline, `review`) already do.
"""

from uuid import UUID

from django.db.models import QuerySet
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.documents.models import DuplicateCandidate
from apps.documents.visibility import visible_documents
from apps.workspaces.models import Membership


def duplicate_not_found() -> ApiError:
    return ApiError(404, "duplicate_not_found", _("We kunnen deze dubbele brieven niet vinden."))


def ordered(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """The two ids in the order a pair stores them (`first_id < second_id`)."""
    return (a, b) if a < b else (b, a)


def visible_pairs(membership: Membership) -> QuerySet[DuplicateCandidate]:
    """The open pairs whose two letters `membership`'s user both sees (trash left out)."""
    letters = visible_documents(membership).values("id")
    return DuplicateCandidate.objects.filter(
        workspace_id=membership.workspace_id,
        state=DuplicateCandidate.State.OPEN,
        first_id__in=letters,
        second_id__in=letters,
    )
