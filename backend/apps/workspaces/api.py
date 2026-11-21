from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Router, Status

from apps.accounts.auth import session_auth
from apps.accounts.schemas import AcceptedInvitationOut, UserOut
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle, ClientAddressThrottle
from apps.pipeline.schemas import AiStatusOut
from apps.workspaces import invitations, members, services, settings_service, visibility_settings
from apps.workspaces.deps import workspace_member
from apps.workspaces.invitations import IssuedInvitation
from apps.workspaces.models import Membership
from apps.workspaces.schemas import (
    AcceptInvitationIn,
    CreatedInvitationOut,
    EmergencyAccessIn,
    EmergencyAccessOut,
    InvitationIn,
    InvitationInfoOut,
    InvitationOut,
    InvitationTokenIn,
    MemberChangeOut,
    MemberOut,
    MemberRoleIn,
    PostAccessOut,
    SortsPostIn,
    VisibilityIn,
    VisibilityOut,
    VisibilityPreviewIn,
    VisibilityPreviewOut,
    WorkspaceOut,
    WorkspaceSettingsIn,
    WorkspaceSettingsOut,
)

router = Router(tags=["workspaces"])

WEB_SESSION_ONLY = "Web session only: a bearer token answers 401 not_authenticated."
ADMINS_ONLY = (
    " For the workspace's admins: another role answers 403 forbidden, a workspace the caller"
    " is not a member of 404 workspace_not_found."
)
# Every new link counts, made or renewed, per admin: the table cannot be flooded.
INVITATION_LINKS = CallerThrottle("invitation_link", "30/10m", per_path=False)
# Opening and accepting share one count per client address.
INVITATION_TRIES = ClientAddressThrottle("invitation", "20/10m")
DEAD_LINK = (
    " 400 invitation_invalid for an unknown, used, expired or revoked link, or one whose"
    " maker is no admin of the workspace any more (one sentence for all)."
)


@router.get(
    "/workspaces",
    response={200: list[WorkspaceOut], **AUTH_ERRORS},
    operation_id="listWorkspaces",
)
def list_workspaces(request: HttpRequest) -> list[WorkspaceOut]:
    memberships = services.list_memberships(current_user(request))
    return [WorkspaceOut.from_membership(m) for m in memberships]


@router.get(
    "/workspaces/{workspace_id}/settings",
    response={200: WorkspaceSettingsOut, 404: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY + " The workspace's settings, for every member.",
    operation_id="getWorkspaceSettings",
)
def get_workspace_settings(request: HttpRequest, workspace_id: UUID) -> WorkspaceSettingsOut:
    membership = workspace_member(request, workspace_id)
    return WorkspaceSettingsOut.of(membership.workspace)


@router.put(
    "/workspaces/{workspace_id}/settings",
    response={200: WorkspaceSettingsOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " Change settings; a setting left out stays as it is. A change is written to the log.",
    operation_id="updateWorkspaceSettings",
)
def update_workspace_settings(
    request: HttpRequest, workspace_id: UUID, payload: WorkspaceSettingsIn
) -> WorkspaceSettingsOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    workspace = settings_service.update_settings(
        workspace=membership.workspace,
        actor=actor_from_request(request),
        allow_member_export=payload.allow_member_export,
        summary_language=payload.summary_language,
        care_is_private=payload.care_is_private,
    )
    return WorkspaceSettingsOut.of(workspace)


def _visibility_out(membership: Membership) -> VisibilityOut:
    view = visibility_settings.visibility_for(membership)
    workspace = view.workspace
    return VisibilityOut(
        kind=workspace.kind,
        everyone_sees_all=workspace.everyone_sees_all,
        departments_enabled=workspace.departments_enabled,
        admins_read_all=workspace.admins_read_all,
        sorter_user_ids=sorted(view.sorter_ids, key=str),
        sentences=view.sentences,
        summary=view.summary,
    )


@router.get(
    "/workspaces/{workspace_id}/visibility",
    response={200: VisibilityOut, 404: ErrorOut, **AUTH_ERRORS},
    description="Who sees what, for every member: the three switches, who sorts post, and"
    ' the sentences of "Wie kan mijn post zien?" for the caller; an admin also gets the'
    " summary of the installation.",
    operation_id="getVisibility",
)
def get_visibility(request: HttpRequest, workspace_id: UUID) -> VisibilityOut:
    membership = workspace_member(request, workspace_id)
    return _visibility_out(membership)


@router.get(
    "/workspaces/{workspace_id}/post-access",
    response={200: PostAccessOut, 404: ErrorOut, **AUTH_ERRORS},
    description='"Wie kan mijn post zien?": who can see the caller\'s post, in sentences'
    " (the switches, the admins, who sorts post, private post, emergency access, the server"
    " admins, the AI service). For every member, also with the app.",
    operation_id="getMyPostAccess",
)
def get_my_post_access(request: HttpRequest, workspace_id: UUID) -> PostAccessOut:
    membership = workspace_member(request, workspace_id)
    return PostAccessOut(sentences=visibility_settings.post_access(membership))


def _proposal(payload: VisibilityPreviewIn | VisibilityIn) -> visibility_settings.Proposal:
    return visibility_settings.Proposal(
        kind=payload.kind,
        everyone_sees_all=payload.everyone_sees_all,
        departments_enabled=payload.departments_enabled,
        admins_read_all=payload.admins_read_all,
        care_is_private=payload.care_is_private,
        allow_member_export=payload.allow_member_export,
        apply_preset=payload.apply_preset,
        add_start_set=payload.add_start_set,
    )


@router.post(
    "/workspaces/{workspace_id}/visibility/preview",
    response={
        200: VisibilityPreviewOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " What a change of the switches, or a planned member change (`member_change`), would"
    " do: per member what they start and stop seeing, the number of letters whose viewers"
    " change, whether it needs confirmation, the summary after it, and the token a"
    " confirmed change sends back. Changes nothing. A member change answers that change's"
    " own errors (404 member_not_found, 409 last_admin, 422 viewer_cannot_sort, ...).",
    operation_id="previewVisibilityChange",
)
def preview_visibility_change(
    request: HttpRequest, workspace_id: UUID, payload: VisibilityPreviewIn
) -> VisibilityPreviewOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    result = visibility_settings.preview_request(
        workspace=membership.workspace,
        proposal=_proposal(payload),
        member_change=visibility_settings.MemberChange(
            kind=payload.member_change,
            user_id=payload.member_user_id,
            person_id=payload.member_person_id,
            role=payload.member_role,
            sorts_post=payload.member_sorts_post,
        ),
        actor=actor_from_request(request),
    )
    return VisibilityPreviewOut(
        changes=[
            MemberChangeOut(
                user_id=c.user_id, name=c.name, gains=list(c.gains), losses=list(c.loses)
            )
            for c in visibility_settings.in_words(result)
        ],
        letter_count=result.letter_count,
        needs_confirmation=result.needs_confirmation,
        sentences=result.sentences,
        preview_token=result.preview_token,
    )


@router.put(
    "/workspaces/{workspace_id}/visibility",
    response={
        200: VisibilityOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " Change who sees what; a value left out stays as it is. When anybody gains sight:"
    " 409 confirmation_required without `confirmed`, 409 preview_outdated when something"
    " changed since the preview whose token is sent. Every changed value is written to the"
    " log; the members whose sight changes get a notice, every member when admins gain all"
    " post. Labels and rules are never removed.",
    operation_id="updateVisibility",
)
def update_visibility(
    request: HttpRequest, workspace_id: UUID, payload: VisibilityIn
) -> VisibilityOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    visibility_settings.change(
        workspace=membership.workspace,
        proposal=_proposal(payload),
        actor=actor_from_request(request),
        confirmed=payload.confirmed,
        preview_token=payload.preview_token,
    )
    return _visibility_out(membership)


@router.put(
    "/workspaces/{workspace_id}/emergency-access",
    response={
        200: EmergencyAccessOut,
        403: ErrorOut,
        404: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + " Switch emergency access for the workspace's admins. Server admins only (403"
    " forbidden), also without a membership; every member gets a notice.",
    operation_id="setEmergencyAccess",
)
def set_emergency_access(
    request: HttpRequest, workspace_id: UUID, payload: EmergencyAccessIn
) -> EmergencyAccessOut:
    workspace = settings_service.set_emergency_access(
        workspace_id=workspace_id,
        enabled=payload.enabled,
        user=current_user(request),
        actor=actor_from_request(request),
    )
    return EmergencyAccessOut(enabled=workspace.emergency_access)


@router.get(
    "/workspaces/{workspace_id}/members",
    response={200: list[MemberOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listMembers",
)
def list_members(request: HttpRequest, workspace_id: UUID) -> list[MemberOut]:
    membership = workspace_member(request, workspace_id)
    return services.list_members(membership.workspace, viewer=current_user(request))


@router.put(
    "/workspaces/{workspace_id}/members/{user_id}/role",
    response={200: MemberOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " Give a member another role; the member sees it at their next request. 404"
    " member_not_found for somebody who is no member of this workspace; 409 last_admin for"
    " demoting the only admin. A demoted admin's open invitations and reset links stop. When"
    " somebody gains sight by it (a new admin while admins read all post, a new fallback"
    " sorter): 409 confirmation_required without `confirmed`, 409 preview_outdated for an"
    " old preview's token (`previewVisibilityChange`). A viewer sorts no post.",
    operation_id="updateMemberRole",
)
def update_member_role(
    request: HttpRequest, workspace_id: UUID, user_id: UUID, payload: MemberRoleIn
) -> MemberOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    members.change_role(
        workspace=membership.workspace,
        user_id=user_id,
        role=payload.role,
        actor=actor_from_request(request),
        confirmed=payload.confirmed,
        preview_token=payload.preview_token,
    )
    viewer = current_user(request)
    return services.list_members(membership.workspace, viewer=viewer, user_id=user_id)[0]


@router.put(
    "/workspaces/{workspace_id}/members/{user_id}/sorts-post",
    response={
        200: MemberOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + ' "Verdeelt post": the member gets the letters nobody else reads, to put a name on'
    " them. 404 member_not_found; 422 viewer_cannot_sort; when somebody gains sight: 409"
    " confirmation_required without `confirmed`, 409 preview_outdated for an old preview's"
    " token (`previewVisibilityChange`).",
    operation_id="updateMemberSortsPost",
)
def update_member_sorts_post(
    request: HttpRequest, workspace_id: UUID, user_id: UUID, payload: SortsPostIn
) -> MemberOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    members.set_sorts_post(
        workspace=membership.workspace,
        user_id=user_id,
        sorts_post=payload.sorts_post,
        actor=actor_from_request(request),
        confirmed=payload.confirmed,
        preview_token=payload.preview_token,
    )
    viewer = current_user(request)
    return services.list_members(membership.workspace, viewer=viewer, user_id=user_id)[0]


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " The member leaves the workspace; their person stays without the account link and"
    " their tasks here lose their assignee. With their last membership the account is"
    " switched off: its sessions end and its devices stop. 403 server_admin_only when that"
    " would switch off a server admin and the caller is none; 404 member_not_found; 409"
    " last_admin for the only admin of the workspace, or the last server admin.",
    operation_id="removeMember",
)
def remove_member(request: HttpRequest, workspace_id: UUID, user_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    members.remove_member(
        request=request,
        workspace=membership.workspace,
        user_id=user_id,
        actor=actor_from_request(request),
        left=False,
    )
    return Status(204, None)


@router.post(
    "/workspaces/{workspace_id}/leave",
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + " The caller leaves the workspace (any role). With their last membership their"
    " account is switched off and this session has ended when the answer comes. 404"
    " workspace_not_found; 409 last_admin for the only admin of the workspace, or the last"
    " server admin.",
    operation_id="leaveWorkspace",
)
def leave_workspace(request: HttpRequest, workspace_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id)
    members.remove_member(
        request=request,
        workspace=membership.workspace,
        user_id=membership.user_id,
        actor=actor_from_request(request),
        left=True,
    )
    return Status(204, None)


def _created(issued: IssuedInvitation) -> CreatedInvitationOut:
    return CreatedInvitationOut(
        invitation=InvitationOut.from_invitation(issued.invitation),
        link=issued.link,
        home_network_only=issued.home_network_only,
    )


@router.post(
    "/workspaces/{workspace_id}/invitations",
    response={
        201: CreatedInvitationOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=INVITATION_LINKS,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " A one-time link to join with the given role; the token is in its fragment and is"
    " shown only here (Cache-Control: no-store). 404 person_not_found; 409"
    " person_already_linked, already_member (the email address is a member's); 422"
    " email_required.",
    operation_id="createInvitation",
)
def create_invitation(
    request: HttpRequest, response: HttpResponse, workspace_id: UUID, payload: InvitationIn
) -> Status[CreatedInvitationOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    issued = invitations.create_invitation(
        membership=membership,
        actor=actor_from_request(request),
        request=request,
        **payload.model_dump(),
    )
    response["Cache-Control"] = "no-store"
    return Status(201, _created(issued))


@router.get(
    "/workspaces/{workspace_id}/invitations",
    response={200: list[InvitationOut], 403: ErrorOut, 404: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " Invitations neither used nor revoked, newest first; status open or expired.",
    operation_id="listInvitations",
)
def list_invitations(request: HttpRequest, workspace_id: UUID) -> list[InvitationOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return [
        InvitationOut.from_invitation(i) for i in invitations.list_invitations(membership.workspace)
    ]


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " The link stops working. 404 invitation_not_found for a used, revoked or unknown one.",
    operation_id="revokeInvitation",
)
def revoke_invitation(
    request: HttpRequest, workspace_id: UUID, invitation_id: UUID
) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    invitations.revoke_invitation(
        workspace=membership.workspace,
        invitation_id=invitation_id,
        actor=actor_from_request(request),
    )
    return Status(204, None)


@router.post(
    "/workspaces/{workspace_id}/invitations/{invitation_id}/renew",
    response={
        201: CreatedInvitationOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    auth=session_auth,
    throttle=INVITATION_LINKS,
    description=WEB_SESSION_ONLY
    + ADMINS_ONLY
    + " A new link with the same role, person, email address, note and validity; the old"
    " link stops. Shown only here (Cache-Control: no-store). 404 invitation_not_found;"
    " 409 and 422 as when making one.",
    operation_id="renewInvitation",
)
def renew_invitation(
    request: HttpRequest, response: HttpResponse, workspace_id: UUID, invitation_id: UUID
) -> Status[CreatedInvitationOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    issued = invitations.renew_invitation(
        membership=membership,
        actor=actor_from_request(request),
        request=request,
        invitation_id=invitation_id,
    )
    response["Cache-Control"] = "no-store"
    return Status(201, _created(issued))


@router.post(
    "/invitations/lookup",
    response={200: InvitationInfoOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=INVITATION_TRIES,
    description="What an invitation link offers, before accepting: the workspace's name,"
    " who invited, the role, whether only one email address can accept, the person and the"
    " workspace's AI line. The token from the link's fragment goes in the body." + DEAD_LINK,
    operation_id="getInvitation",
)
def get_invitation(request: HttpRequest, payload: InvitationTokenIn) -> InvitationInfoOut:
    info = invitations.lookup_invitation(token=payload.token)
    return InvitationInfoOut(
        workspace_name=info.workspace_name,
        invited_by=info.invited_by,
        role=info.role,
        email_fixed=info.email_fixed,
        person_name=info.person_name,
        ai=AiStatusOut.from_status(info.ai, with_model=False, autopilot=info.autopilot),
    )


@router.post(
    "/invitations/accept",
    response={
        200: AcceptedInvitationOut,
        401: ErrorOut,
        403: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **BODY_ERRORS,
    },
    auth=None,
    throttle=INVITATION_TRIES,
    description="Join with the invitation's role and sign in. account `new`: name, email"
    " address and password (the rules of every account; 409 account_exists when the"
    " address has an account, which says that it has one, as registering does). account"
    " `existing`: the account's email address and password, with the sign-in lockout (401"
    " invalid_credentials, 429 too_many_attempts); a deactivated account is reactivated."
    " 403 invitation_email_mismatch when the invitation is for another address; 409"
    " already_member, invitation_person_taken; 422 for another account kind." + DEAD_LINK,
    operation_id="acceptInvitation",
)
def accept_invitation(
    request: HttpRequest, response: HttpResponse, payload: AcceptInvitationIn
) -> AcceptedInvitationOut:
    membership = invitations.accept_invitation(request, **payload.model_dump())
    response["Cache-Control"] = "no-store"
    return AcceptedInvitationOut(
        user=UserOut.model_validate(membership.user, from_attributes=True),
        workspace=WorkspaceOut.from_membership(membership),
    )
