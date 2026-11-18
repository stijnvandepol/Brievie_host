"""Seams for private post (part B of phase 1d). In part A every one is a no-op; each is
called from exactly one place, so part B fills them in without touching the callers."""

from uuid import UUID

from apps.people.models import Person


def person_requires_email(person: Person) -> bool:
    """Whether an invitation for `person` must name an email address.

    Part B: True when the person has restricted (private) documents, because accepting
    the invitation then gives access to them. Always False in part A.
    """
    return False


def on_member_added(
    workspace_id: UUID, user_id: UUID, *, person_id: UUID | None, created_by_id: UUID | None
) -> None:
    """Called when an invitation is accepted, inside its transaction.

    Part B: the person now linked to the user (`person_id`) makes the user a reader of
    that person's private documents; the grant is recorded with the invitation's maker
    (`created_by_id`) as actor. Nothing in part A.
    """


def on_member_removed(workspace_id: UUID, user_id: UUID) -> None:
    """Called when a member leaves or is removed, inside that transaction.

    Part B: delete the member's reader rows, shares and department memberships, as the
    system. Nothing in part A.
    """
