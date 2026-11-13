from uuid import UUID

from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.services import current_user
from apps.common.errors import ApiError
from apps.workspaces.context import enter_workspace_for_transaction
from apps.workspaces.models import Membership


def workspace_member(
    request: HttpRequest, workspace_id: UUID, *, min_role: str = Membership.Role.VIEWER
) -> Membership:
    """The caller's membership of the workspace in the URL, or an API error.

    A workspace the caller is not a member of answers exactly like one that does
    not exist (404 `workspace_not_found`), so ids reveal nothing. A role below
    `min_role` gives 403 `forbidden`. On success row-level security is scoped to
    this workspace for the rest of the request transaction.
    """
    if min_role not in Membership.RANK:
        raise ValueError(f"Unknown role: {min_role!r}")
    membership = (
        Membership.objects.select_related("workspace")
        .filter(user=current_user(request), workspace_id=workspace_id)
        .first()
    )
    if membership is None:
        raise ApiError(404, "workspace_not_found", _("We kunnen deze werkruimte niet vinden."))
    if not membership.has_role(min_role):
        raise ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))
    enter_workspace_for_transaction(workspace_id)
    return membership
