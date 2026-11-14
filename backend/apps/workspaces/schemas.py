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
