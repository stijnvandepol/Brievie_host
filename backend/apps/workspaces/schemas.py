from datetime import datetime
from typing import Literal
from uuid import UUID

from django.utils import timezone
from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema
from apps.pipeline.schemas import AiStatusOut
from apps.workspaces.models import Invitation, Membership


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


class WorkspaceSettingsOut(Schema):
    # Whether a member may export their own post.
    allow_member_export: bool


class WorkspaceSettingsIn(InputSchema):
    # A setting left out stays as it is.
    allow_member_export: bool | None = None


class MemberRoleIn(InputSchema):
    role: Literal["admin", "member", "viewer"]


class InvitationIn(InputSchema):
    role: Literal["admin", "member", "viewer"] = "member"
    # How long the link works: 1, 7 or 30 days.
    expires_in_days: Literal[1, 7, 30] = 7
    # Optional: only an account with this address can accept.
    email: str = Field(default="", max_length=254)
    # Optional: the person (without an account link) the new member is linked to.
    person_id: UUID | None = None
    # Optional: the admin's own name for it; never shown to the invitee.
    note: str = Field(default="", max_length=100)


class InvitationOut(Schema):
    """An open invitation, for admins. The token is never read back."""

    id: UUID
    role: str
    email: str
    note: str
    person_id: UUID | None
    person_name: str = ""
    created_by_name: str
    created_at: datetime
    expires_at: datetime
    # `open` or `expired`.
    status: str

    @classmethod
    def from_invitation(cls, invitation: Invitation) -> "InvitationOut":
        """Needs the workspace's context when the invitation names a person."""
        person = invitation.person
        return cls(
            id=invitation.id,
            role=invitation.role,
            email=invitation.email,
            note=invitation.note,
            person_id=invitation.person_id,
            person_name=person.display_name if person is not None else "",
            created_by_name=invitation.created_by_name,
            created_at=invitation.created_at,
            expires_at=invitation.expires_at,
            status="expired" if invitation.expires_at <= timezone.now() else "open",
        )


class CreatedInvitationOut(Schema):
    invitation: InvitationOut
    # The link, with its one-time token in the fragment. Shown once.
    link: str
    # True when every address of the server is a home-network one: the link then works
    # only at home.
    home_network_only: bool


class InvitationTokenIn(InputSchema):
    # The fragment of the invitation link.
    token: str = Field(max_length=100)


class InvitationInfoOut(Schema):
    """What the invitee sees before accepting; nothing else about the workspace."""

    workspace_name: str
    # The name of who made the invitation, as it was then.
    invited_by: str
    # `admin`, `member` or `viewer`: fixed by the invitation.
    role: str
    # Whether only one email address can accept (the address itself is not shown).
    email_fixed: bool
    # The person the new member becomes ("Papa"), if the invitation names one.
    person_name: str = ""
    # The workspace's AI line, as every member sees it: where the text of letters goes.
    ai: AiStatusOut


class AcceptInvitationIn(InputSchema):
    token: str = Field(max_length=100)
    # `new` (create an account) or `existing` (sign in to one with its password).
    account: str = Field(max_length=10)
    # For a new account.
    name: str = Field(default="", max_length=150)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
