from collections.abc import Sequence
from uuid import UUID

from django.db import transaction
from django.db.models import Count

from apps.accounts.authority import actor_reads_all_post
from apps.accounts.models import User
from apps.labels.presets import apply_preset
from apps.labels.services import create_extra_labels
from apps.people.models import DepartmentMember, Person
from apps.people.services import ensure_person_for_member
from apps.workspaces import access
from apps.workspaces.context import NOBODY, workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.presets import apply_preset_settings
from apps.workspaces.schemas import MemberOut


def _new_workspace(*, name: str, kind: str) -> Workspace:
    """The workspace row with its kind's values (`presets.apply_preset_settings`)."""
    if kind not in Workspace.Kind.values:
        raise ValueError(f"Unknown workspace kind: {kind!r}")
    workspace = Workspace(name=name, kind=kind)
    apply_preset_settings(workspace)
    # "Blijft liggen" is for an organisation (navigation spec S 6, B3).
    workspace.lingering_enabled = kind == Workspace.Kind.BUSINESS
    workspace.save()
    return workspace


def create_workspace(
    *, name: str, kind: str, admin: User, extra_labels: Sequence[str] = ()
) -> Workspace:
    """The workspace with its admin, the kind's values (Thuis: everyone sees all post;
    Organisatie: own post, departments, R40), the start set of labels and `extra_labels`
    as own labels."""
    with transaction.atomic():
        workspace = _new_workspace(name=name, kind=kind)
        Membership.objects.create(user=admin, workspace=workspace, role=Membership.Role.ADMIN)
        with workspace_context(workspace.id, actor=admin.id):
            workspace_key(workspace.id)
            ensure_person_for_member(workspace=workspace, user=admin)
            apply_preset(workspace)
            create_extra_labels(workspace=workspace, names=extra_labels)
    return workspace


def create_workspace_without_admin(*, name: str, kind: str) -> Workspace:
    """A workspace whose first admin is still to be invited (`manage.py
    create_workspace` for an address without an account): the kind's values, the key and
    the start set; no member yet."""
    with transaction.atomic():
        workspace = _new_workspace(name=name, kind=kind)
        with workspace_context(workspace.id, actor=NOBODY):
            workspace_key(workspace.id)
            apply_preset(workspace)
    return workspace


def list_memberships(user: User) -> list[Membership]:
    return list(
        Membership.objects.filter(user=user)
        .select_related("workspace")
        .order_by("workspace__name", "workspace__id")
    )


def list_members(
    workspace: Workspace, *, viewer: User, user_id: UUID | None = None
) -> list[MemberOut]:
    """The workspace's members by name (only `user_id` when given), each with their linked
    person (needs the RLS context) and whether `viewer` may act on their account: the rule
    of `account_authority` (a server admin for anyone else; a workspace admin only for a
    member of this workspace alone who is no server admin, who reads no private post,
    while the viewer reads all post that is not private (S 4.19); nobody for their own
    account). The private-post readers in one query."""
    person_ids = dict(
        Person.objects.filter(workspace=workspace).exclude(user=None).values_list("user_id", "id")
    )
    departments: dict[UUID, list[UUID]] = {}
    for member_id, department_id in DepartmentMember.objects.filter(
        workspace=workspace
    ).values_list("user_id", "department_id"):
        departments.setdefault(member_id, []).append(department_id)
    rows = Membership.objects.filter(workspace=workspace)
    if user_id is not None:
        rows = rows.filter(user_id=user_id)
    memberships = list(rows.select_related("user").order_by("user__name", "user__id"))
    viewer_administers = viewer.is_active and (
        Membership.objects.filter(
            user=viewer, workspace=workspace, role=Membership.Role.ADMIN
        ).exists()
    )
    readers_of_private_post = (
        access.private_post_readers(workspace.id) if viewer_administers else set()
    )
    viewer_reads_all = actor_reads_all_post(workspace, actor_is_admin=viewer_administers)
    counts = dict(
        Membership.objects.filter(user_id__in=[m.user_id for m in memberships])
        .values("user_id")
        .annotate(total=Count("id"))
        .values_list("user_id", "total")
    )

    def may_manage(member: Membership) -> bool:
        target = member.user
        if target.id == viewer.id or not viewer.is_active:
            return False
        if viewer.is_server_admin:
            return True
        return bool(
            viewer_administers
            and counts.get(target.id) == 1
            and not target.is_server_admin
            and viewer_reads_all
            and target.id not in readers_of_private_post
        )

    return [
        MemberOut(
            user_id=m.user_id,
            name=m.user.name,
            role=m.role,
            person_id=person_ids.get(m.user_id),
            can_manage_account=may_manage(m),
            sorts_post=m.sorts_post,
            department_ids=sorted(departments.get(m.user_id, []), key=str),
        )
        for m in memberships
    ]
