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
from apps.workspaces import invitations, members, services
from apps.workspaces.deps import workspace_member
from apps.workspaces.invitations import IssuedInvitation
from apps.workspaces.models import Membership
from apps.workspaces.schemas import (
    AcceptInvitationIn,
    CreatedInvitationOut,
    InvitationIn,
    InvitationInfoOut,
    InvitationOut,
    InvitationTokenIn,
    MemberOut,
    MemberRoleIn,
    WorkspaceOut,
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
    " demoting the only admin. A demoted admin's open invitations and reset links stop.",
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
        ai=AiStatusOut.from_status(info.ai, with_model=False),
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
