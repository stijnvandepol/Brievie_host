from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Router, Status

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle
from apps.workspaces import invitations, services
from apps.workspaces.deps import workspace_member
from apps.workspaces.invitations import IssuedInvitation
from apps.workspaces.models import Membership
from apps.workspaces.schemas import (
    CreatedInvitationOut,
    InvitationIn,
    InvitationOut,
    MemberOut,
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
