from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.people import services
from apps.people.models import Person
from apps.people.schemas import PersonIn, PersonMemberIn, PersonOut
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
)
def create_person(request: HttpRequest, workspace_id: UUID, payload: PersonIn) -> Status[Person]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    person = services.create_person(
        workspace=membership.workspace,
        display_name=payload.display_name,
        name_variants=payload.name_variants,
        archived=payload.archived,
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
        "never archives, restores or clears the variants); sent, they replace it."
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
        workspace=membership.workspace, person_id=person_id, actor=actor_from_request(request)
    )
    return Status(204, None)


@router.put(
    f"{_PERSON}/member",
    response={200: PersonOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="linkPersonToMember",
)
def link_person_to_member(
    request: HttpRequest, workspace_id: UUID, person_id: UUID, payload: PersonMemberIn
) -> Person:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.link_member(
        workspace=membership.workspace,
        person_id=person_id,
        user_id=payload.user_id,
        actor=actor_from_request(request),
    )
