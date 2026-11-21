"""A member's facts for "Mijn taken", read once per request (spec S 5.1, S 11).

Through the same SQL functions the letter policy uses (`visibility_sql`), so there is no
second definition of "my post": the persons and departments linked to the member, whether
they sort post, and which addressees somebody reads through.
"""

from uuid import UUID

from django.db import connection

from apps.people.models import Person
from apps.tasks.mine import MemberFacts
from apps.workspaces.context import current_actor
from apps.workspaces.models import Membership, Workspace


def member_facts(membership: Membership) -> MemberFacts:
    """The facts of `membership`'s user, read as that user (else `RuntimeError`, like the
    document seam: as anyone else the functions would answer about someone else)."""
    if current_actor() != membership.user_id:
        raise RuntimeError("Mijn taken reads the facts only as the member they are about.")
    user_id = membership.user_id
    with connection.cursor() as c:
        c.execute(
            "SELECT public.brievie_my_post_ids(%s), public.brievie_actor_sorts_post(%s),"
            " CASE WHEN public.brievie_actor_sorts_post(%s)"
            " THEN public.brievie_covered_post_ids() END",
            [user_id, user_id, user_id],
        )
        row = c.fetchone()
    post_ids: list[UUID] = list(row[0]) if row is not None and row[0] else []
    sorts = bool(row[1]) if row is not None else False
    covered: list[UUID] = list(row[2]) if row is not None and row[2] else []
    departments = (
        set(
            Person.objects.filter(id__in=post_ids, kind=Person.Kind.DEPARTMENT).values_list(
                "id", flat=True
            )
        )
        if post_ids
        else set()
    )
    return MemberFacts(
        user_id=user_id,
        person_ids=frozenset(p for p in post_ids if p not in departments),
        department_ids=frozenset(departments),
        sorts_post=sorts,
        covered_ids=frozenset(covered) if sorts else frozenset(),
        split=membership.workspace.kind == Workspace.Kind.BUSINESS,
    )
