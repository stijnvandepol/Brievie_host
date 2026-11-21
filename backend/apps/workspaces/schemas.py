from datetime import datetime
from typing import Literal
from uuid import UUID

from django.utils import timezone
from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema
from apps.pipeline.schemas import AiStatusOut
from apps.workspaces.models import Invitation, Membership, Workspace


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
    # "Verdeelt post": the member gets the letters nobody else reads (S 4.1).
    sorts_post: bool = False
    # The departments the member is in.
    department_ids: list[UUID] = Field(default_factory=list)


class WorkspaceSettingsOut(Schema):
    # Whether a member may export their own post.
    allow_member_export: bool
    # The language of the AI's summaries and task descriptions: "nl" or "en".
    summary_language: str = "nl"
    # A letter labelled Zorg becomes private on its own (at home on, in an organisation off).
    care_is_private: bool = False
    # An admin may open someone's hidden post for an hour with a reason (only a server
    # admin switches it, `setEmergencyAccess`).
    emergency_access: bool = False

    # Who sees what (S 4.1); changed through `updateVisibility`, never here.
    everyone_sees_all: bool = True
    departments_enabled: bool = False
    admins_read_all: bool = True

    @staticmethod
    def of(workspace: Workspace) -> "WorkspaceSettingsOut":
        return WorkspaceSettingsOut(
            allow_member_export=workspace.allow_member_export,
            summary_language=workspace.summary_language,
            care_is_private=workspace.care_is_private,
            emergency_access=workspace.emergency_access,
            everyone_sees_all=workspace.everyone_sees_all,
            departments_enabled=workspace.departments_enabled,
            admins_read_all=workspace.admins_read_all,
        )


class WorkspaceSettingsIn(InputSchema):
    # A setting left out stays as it is.
    allow_member_export: bool | None = None
    # "nl" or "en" (a pattern, not an enum: the contract has no nullable enums).
    summary_language: str | None = Field(default=None, pattern="^(nl|en)$")
    # A Zorg label makes a letter private; changes no letter that has it already.
    care_is_private: bool | None = None


class EmergencyAccessIn(InputSchema):
    enabled: bool


class EmergencyAccessOut(Schema):
    enabled: bool


class ConfirmedIn(InputSchema):
    # A change by which somebody gains sight needs these (S 4.4): the admin confirmed,
    # with the token of the preview (`previewVisibilityChange`) that was confirmed.
    confirmed: bool = False
    preview_token: str = Field(default="", max_length=64)


class MemberRoleIn(ConfirmedIn):
    role: Literal["admin", "member", "viewer"]


class SortsPostIn(ConfirmedIn):
    sorts_post: bool


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


class VisibilitySettingsIn(InputSchema):
    """A change of who sees what (S 4.13). A value left out (null) stays as it is.
    Emergency access is not here: only the server admin switches it."""

    # "home" (Thuis) or "business" (Organisatie): the kind changes no right by itself.
    kind: str | None = Field(default=None, pattern="^(home|business)$")
    everyone_sees_all: bool | None = None
    departments_enabled: bool | None = None
    admins_read_all: bool | None = None
    care_is_private: bool | None = None
    allow_member_export: bool | None = None
    # From the preset dialog: the kind's values where a value is left out, and one
    # `workspace_preset_applied` row in the log.
    apply_preset: bool = False
    # Also add the kind's start set of labels and rules (removes nothing).
    add_start_set: bool = False


class VisibilityIn(VisibilitySettingsIn):
    # The admin confirmed that people gain sight; needed when anybody gains (409
    # confirmation_required otherwise).
    confirmed: bool = False
    # The `preview_token` of the preview that was confirmed (409 preview_outdated when
    # something changed since).
    preview_token: str = Field(default="", max_length=64)


class VisibilityPreviewIn(VisibilitySettingsIn):
    # A planned member change instead of a settings change: "role", "sorts_post",
    # "department_add", "department_remove", "link", "unlink" or "remove"; "" for none.
    member_change: str = Field(
        default="",
        pattern="^(|role|sorts_post|department_add|department_remove|link|unlink|remove)$",
    )
    # The member concerned (role, sorts_post, department_add/remove, link, remove).
    member_user_id: UUID | None = None
    # The person or department concerned (department_add/remove, link, unlink).
    member_person_id: UUID | None = None
    # The new role (role).
    member_role: str = Field(default="", pattern="^(|admin|member|viewer)$")
    # The new flag (sorts_post).
    member_sorts_post: bool = False


class MemberChangeOut(Schema):
    user_id: UUID
    name: str
    # The kinds of post the member starts or stops seeing, in words ("post van Finance").
    gains: list[str]
    losses: list[str]


class VisibilityPreviewOut(Schema):
    # Per member whose sight changes, by name.
    changes: list[MemberChangeOut]
    # Letters whose set of viewers changes.
    letter_count: int
    # The change can only be saved with `confirmed` and this preview's token.
    needs_confirmation: bool
    # The summary sentences of the state after the change.
    sentences: list[str]
    preview_token: str


class VisibilityOut(Schema):
    kind: str
    everyone_sees_all: bool
    departments_enabled: bool
    admins_read_all: bool
    # Who sorts post now: the flagged members, or the admins when nobody is flagged.
    sorter_user_ids: list[UUID]
    # "Wie kan mijn post zien?" for the caller.
    sentences: list[str]
    # For admins: the summary of the installation (the head of "Wie ziet wat"); empty
    # for everyone else.
    summary: list[str] = Field(default_factory=list)


class PostAccessOut(Schema):
    """ "Wie kan mijn post zien?" for the caller, in sentences the server writes."""

    sentences: list[str]
