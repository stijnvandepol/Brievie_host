from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.accounts.auth import session_auth
from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.people import services
from apps.people.models import Person
from apps.people.schemas import (
    DepartmentMemberIn,
    DepartmentMemberUpdateIn,
    EmergencyAccessUseIn,
    EmergencyAccessUseOut,
    PersonIn,
    PersonMemberIn,
    PersonOut,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["people"])

_PERSONS = "/workspaces/{workspace_id}/persons"
_PERSON = "/workspaces/{workspace_id}/persons/{person_id}"


@router.get(
    _PERSONS,
    response={200: list[PersonOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listPersons",
)
def list_persons(request: HttpRequest, workspace_id: UUID) -> list[Person]:
    membership = workspace_member(request, workspace_id)
    return services.list_persons(membership.workspace)


@router.post(
    _PERSONS,
    response={201: PersonOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createPerson",
    description='`kind` "department" makes a department: admins only (403 `forbidden`).',
)
def create_person(request: HttpRequest, workspace_id: UUID, payload: PersonIn) -> Status[Person]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    person = services.create_person(
        workspace=membership.workspace,
        display_name=payload.display_name,
        name_variants=payload.name_variants,
        archived=payload.archived,
        kind=payload.kind,
        actor_role=membership.role,
    )
    return Status(201, person)


def sent_fields(payload: PersonIn) -> set[str]:
    """The fields the client sent; a field it left out keeps the stored value on update."""
    return set(payload.model_fields_set)


@router.put(
    _PERSON,
    response={200: PersonOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="updatePerson",
    description=(
        "`name_variants` and `archived` left out keep the person's current value (a rename "
        "never archives, restores or clears the variants); sent, they replace it. A "
        "department or a person linked to an account: admins only (403 `forbidden`)."
    ),
)
def update_person(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, payload: PersonIn
) -> Person:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    sent = sent_fields(payload)
    return services.update_person(
        workspace=membership.workspace,
        person_id=person_id,
        display_name=payload.display_name,
        name_variants=payload.name_variants if "name_variants" in sent else None,
        archived=payload.archived if "archived" in sent else None,
        actor_role=membership.role,
    )


@router.delete(
    _PERSON,
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="deletePerson",
    description=(
        "Admins only (403 `forbidden` for others): `person_in_use` also counts letters the "
        "caller cannot see, so only an admin learns that a person is in use."
    ),
)
def delete_person(request: HttpRequest, workspace_id: UUID, person_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    services.delete_person(
        workspace=membership.workspace,
        person_id=person_id,
        actor=actor_from_request(request),
        viewer=membership,
    )
    return Status(204, None)


@router.put(
    f"{_PERSON}/member",
    response={200: PersonOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    description=(
        "Web session only: a bearer token answers 401 not_authenticated. Link the person "
        "to a member of the workspace (`user_id`; administrators only), or unlink it "
        "(null). A linked person is unlinked only by its own member (409 `cannot_unlink` "
        "for anybody else); 409 `cannot_grant_self` for an admin linking themselves to a "
        "person with private post; 409 `is_a_department`."
    ),
    operation_id="linkPersonToMember",
)
def link_person_to_member(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, payload: PersonMemberIn
) -> Person:
    membership = workspace_member(request, workspace_id)
    return services.link_member(
        workspace=membership.workspace,
        person_id=person_id,
        user_id=payload.user_id,
        actor=actor_from_request(request),
        actor_role=membership.role,
    )


_MEMBERS = f"{_PERSON}/members"
_DEPARTMENT_WRITE = {
    200: PersonOut,
    404: ErrorOut,
    409: ErrorOut,
    **SESSION_WRITE_ERRORS,
    **BODY_ERRORS,
}


@router.post(
    _MEMBERS,
    response=_DEPARTMENT_WRITE,
    auth=session_auth,
    operation_id="addDepartmentMember",
    description=(
        "Web session only. Admins put a member in a department (a grant). 409 "
        "`not_a_department`, 409 `cannot_grant_self` (an admin adding themselves where "
        "there is private post). A member already in it: 200, unchanged."
    ),
)
def add_department_member(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, payload: DepartmentMemberIn
) -> Person:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.add_department_member(
        workspace=membership.workspace,
        person_id=person_id,
        user_id=payload.user_id,
        is_lead=payload.is_lead,
        actor=actor_from_request(request),
    )


@router.put(
    f"{_MEMBERS}/{{user_id}}",
    response=_DEPARTMENT_WRITE,
    auth=session_auth,
    operation_id="updateDepartmentMember",
    description="Web session only. Admins make a department member its lead, or not.",
)
def update_department_member(
    request: HttpRequest,
    workspace_id: UUID,
    person_id: UUID,
    user_id: UUID,
    payload: DepartmentMemberUpdateIn,
) -> Person:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.update_department_member(
        workspace=membership.workspace,
        person_id=person_id,
        user_id=user_id,
        is_lead=payload.is_lead,
        actor=actor_from_request(request),
    )


@router.delete(
    f"{_MEMBERS}/{{user_id}}",
    response={200: PersonOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    operation_id="removeDepartmentMember",
    description="Web session only. Admins take a member out of a department.",
)
def remove_department_member(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, user_id: UUID
) -> Person:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.remove_department_member(
        workspace=membership.workspace,
        person_id=person_id,
        user_id=user_id,
        actor=actor_from_request(request),
    )


@router.post(
    f"{_PERSON}/emergency-access",
    response={
        200: EmergencyAccessUseOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    operation_id="openEmergencyAccess",
    description=(
        "Web session only, admins. Opens for one hour, read-only, every letter on the name of"
        " this person or department that the admin cannot see, with a reason of 10 to 200"
        " characters: a history line on each, an audit row, a notice to the person or the"
        " department's members. 409 `emergency_access_off` when the workspace has it off."
    ),
)
def open_emergency_access(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, payload: EmergencyAccessUseIn
) -> EmergencyAccessUseOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    result = services.open_emergency_access(
        workspace=membership.workspace,
        person_id=person_id,
        reason=payload.reason,
        actor=actor_from_request(request),
    )
    return EmergencyAccessUseOut(opened_count=result.opened_count, expires_at=result.expires_at)
