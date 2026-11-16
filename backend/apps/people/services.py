from uuid import UUID

from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.people.models import Person
from apps.rules.definition import named_ids
from apps.rules.models import Rule
from apps.workspaces.models import Membership, Workspace

_NAME_UNIQUE = "people_person_name_unique"
_USER_UNIQUE = "people_person_user_unique"

NAME_MAX = 150
VARIANTS_MAX = 20
VARIANT_MIN = 2
VARIANT_MAX = 100


def _persons(workspace: Workspace) -> "models.QuerySet[Person]":
    """The workspace's persons, each annotated with `document_count`."""
    return Person.objects.filter(workspace=workspace).annotate(
        document_count=models.Count("document_links")
    )


def _in_use(person: Person) -> bool:
    """Whether deleting would lose information; archiving is the alternative."""
    return person.user_id is not None or person.document_links.exists()


def list_persons(workspace: Workspace) -> list[Person]:
    """Active persons by name, then archived ones; needs the workspace's RLS context."""
    return list(
        _persons(workspace).order_by(
            models.ExpressionWrapper(
                models.Q(archived_at__isnull=False), output_field=models.BooleanField()
            ).asc(),
            Lower("display_name"),
            "id",
        )
    )


def _get_for_update(workspace: Workspace, person_id: UUID) -> Person:
    person = Person.objects.select_for_update().filter(workspace=workspace, id=person_id).first()
    if person is None:
        raise ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
    return person


def _annotated(workspace: Workspace, person_id: UUID) -> Person:
    return _persons(workspace).get(id=person_id)


def _clean_name(display_name: str) -> str:
    name = mask_bsns(display_name).strip()
    if not name:
        raise ApiError(422, "validation_error", _("Een persoon heeft een naam nodig."))
    if len(name) > NAME_MAX:
        raise ApiError(
            422, "validation_error", _("Een naam is hoogstens %(max)d tekens.") % {"max": NAME_MAX}
        )
    return name


def _clean_variants(name_variants: list[str]) -> list[str]:
    """Trimmed, masked and without repeats (ignoring case); in the order given."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in name_variants:
        variant = mask_bsns(raw).strip()
        if not VARIANT_MIN <= len(variant) <= VARIANT_MAX:
            raise ApiError(
                422,
                "validation_error",
                _("Een schrijfwijze van een naam is %(min)d tot %(max)d tekens.")
                % {"min": VARIANT_MIN, "max": VARIANT_MAX},
            )
        if variant.casefold() in seen:
            continue
        seen.add(variant.casefold())
        cleaned.append(variant)
    if len(cleaned) > VARIANTS_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Een persoon heeft hoogstens %(max)d schrijfwijzen.") % {"max": VARIANTS_MAX},
        )
    return cleaned


def _unique_violation(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    if isinstance(cause, pg_errors.UniqueViolation):
        return cause.diag.constraint_name
    return None


def _save(person: Person) -> None:
    """Save, turning a unique violation into the API error that explains it."""
    try:
        with transaction.atomic():
            person.save()
    except IntegrityError as exc:
        constraint = _unique_violation(exc)
        if constraint == _NAME_UNIQUE:
            raise ApiError(409, "person_exists", _("Er is al een persoon met deze naam.")) from exc
        if constraint == _USER_UNIQUE:
            raise _member_already_linked() from exc
        raise


def _member_already_linked() -> ApiError:
    return ApiError(409, "member_already_linked", _("Dit lid hoort al bij een andere persoon."))


def create_person(
    *, workspace: Workspace, display_name: str, name_variants: list[str], archived: bool = False
) -> Person:
    with transaction.atomic():
        person = Person(
            workspace=workspace,
            display_name=_clean_name(display_name),
            name_variants=_clean_variants(name_variants),
            archived_at=timezone.now() if archived else None,
        )
        _save(person)
        return _annotated(workspace, person.id)


def update_person(
    *,
    workspace: Workspace,
    person_id: UUID,
    display_name: str,
    name_variants: list[str],
    archived: bool,
) -> Person:
    with transaction.atomic():
        person = _get_for_update(workspace, person_id)
        person.display_name = _clean_name(display_name)
        person.name_variants = _clean_variants(name_variants)
        if not archived:
            person.archived_at = None
        elif person.archived_at is None:
            person.archived_at = timezone.now()
        _save(person)
        return _annotated(workspace, person.id)


def delete_person(*, workspace: Workspace, person_id: UUID) -> None:
    with transaction.atomic():
        person = _get_for_update(workspace, person_id)
        if _in_use(person):
            raise ApiError(
                409,
                "person_in_use",
                _("Deze persoon is nog in gebruik. Je kunt de persoon wel archiveren."),
            )
        for rule in Rule.objects.filter(workspace=workspace):
            if person.id in named_ids(rule)[1]:
                raise ApiError(
                    409,
                    "person_in_use",
                    _("De regel '%(rule)s' gebruikt deze persoon. Pas die regel eerst aan.")
                    % {"rule": rule.name},
                )
        person.delete()


def _free_name(workspace: Workspace, name: str) -> str:
    """`name`, or `name (2)`, `name (3)`, ... when taken (ignoring case); at most 150 chars."""
    taken = {
        n.lower()
        for n in Person.objects.filter(workspace=workspace).values_list("display_name", flat=True)
    }
    candidate = name[:NAME_MAX]
    number = 2
    while candidate.lower() in taken:
        suffix = f" ({number})"
        candidate = f"{name[: NAME_MAX - len(suffix)]}{suffix}"
        number += 1
    return candidate


def ensure_person_for_member(*, workspace: Workspace, user: User) -> Person:
    """The person linked to `user`, created from the user's name when there is none.

    Needs the workspace's RLS context. The name is not refused when it does not fit
    the rules for typed names: the member exists either way.
    """
    with transaction.atomic():
        person = Person.objects.filter(workspace=workspace, user=user).first()
        if person is not None:
            return person
        name = mask_bsns(user.name).strip()
        return Person.objects.create(
            workspace=workspace,
            display_name=_free_name(workspace, name),
            name_variants=[name] if VARIANT_MIN <= len(name) <= VARIANT_MAX else [],
            user=user,
        )


def link_member(*, workspace: Workspace, person_id: UUID, user_id: UUID | None) -> Person:
    """Link the person to a member of the workspace, or unlink it (`user_id` None)."""
    with transaction.atomic():
        person = _get_for_update(workspace, person_id)
        if user_id is not None:
            if not Membership.objects.filter(workspace=workspace, user_id=user_id).exists():
                raise ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))
            if (
                Person.objects.filter(workspace=workspace, user_id=user_id)
                .exclude(id=person.id)
                .exists()
            ):
                raise _member_already_linked()
        person.user_id = user_id
        _save(person)
        return _annotated(workspace, person.id)
