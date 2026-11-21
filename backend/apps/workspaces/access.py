"""Seams for private post (PB T8): each is called from exactly one place in part A
(invitations, member removal, account actions), so the callers never learn how private
post works. Each runs as the system: it answers about letters the caller may not see,
and only with a yes or no, never with what they hold."""

from uuid import UUID

from apps.accounts.models import User
from apps.people.models import Person
from apps.workspaces.context import SYSTEM, system_actor, workspace_context


def person_requires_email(person: Person) -> bool:
    """Whether an invitation for `person` must name an email address: accepting it gives
    access to the person's private letters (D 8.1)."""
    from apps.documents import visibility

    with system_actor():
        return visibility.has_private_post(person)


def on_member_added(
    workspace_id: UUID, user_id: UUID, *, person_id: UUID | None, created_by_id: UUID | None
) -> None:
    """An invitation was accepted, inside its transaction: the person now linked to the
    user (`person_id`) makes the user a reader of that person's private letters, and the
    grant is recorded with the invitation's maker (`created_by_id`) as actor."""
    if person_id is None:
        return
    from apps.documents import visibility
    from apps.tasks.assignment import release_unseeing_assignees

    with system_actor():
        person = Person.objects.filter(workspace_id=workspace_id, id=person_id).first()
        user = User.objects.filter(id=user_id).first()
        if person is None or user is None:
            return
        visibility.recompute_where(workspace_id=workspace_id, person_id=person_id)
        visibility.record_grant(person, grantee=user, actor_user_id=created_by_id)
        # The person's post is the new member's now: whoever sorted it no longer sees it,
        # and their assignments there end at once (Mijn taken S 5.3).
        release_unseeing_assignees(workspace_id=workspace_id)


def on_member_removed(workspace_id: UUID, user_id: UUID) -> None:
    """A member leaves or is removed, inside that transaction: their reader rows, shares
    and department memberships go (the person link went before this call)."""
    from apps.documents import visibility
    from apps.tasks.assignment import release_unseeing_assignees

    visibility.forget_member(workspace_id, user_id)
    # The member's own assignments went with `release_member`; nobody else lost sight,
    # but the release keeps the rule "no assignment outlives sight" in one place.
    release_unseeing_assignees(workspace_id=workspace_id)


def reads_private_post(workspace_id: UUID, user_id: UUID) -> bool:
    """Whether the user reads a private letter of the workspace (not by emergency
    access). Callable outside any workspace context (account actions)."""
    from apps.documents import visibility

    with workspace_context(workspace_id, actor=SYSTEM):
        return visibility.reads_private_post(user_id)


def private_post_readers(workspace_id: UUID) -> set[UUID]:
    """The users who read a private letter of the workspace; one query."""
    from apps.documents import visibility

    with workspace_context(workspace_id, actor=SYSTEM):
        return visibility.private_post_readers()
