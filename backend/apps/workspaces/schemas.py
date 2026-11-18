from uuid import UUID

from ninja import Schema

from apps.workspaces.models import Membership


class WorkspaceOut(Schema):
    id: UUID
    name: str
    kind: str
    role: str

    @classmethod
    def from_membership(cls, membership: Membership) -> "WorkspaceOut":
        workspace = membership.workspace
        return cls(id=workspace.id, name=workspace.name, kind=workspace.kind, role=membership.role)


class MemberOut(Schema):
    user_id: UUID
    name: str
    role: str
    # The person linked to this member, if any.
    person_id: UUID | None
    # Whether the caller may act on this account (reset link, sessions, devices): the
    # rule of apps.accounts.authority.
    can_manage_account: bool = False
