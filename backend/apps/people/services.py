import unicodedata
from collections.abc import Collection
from uuid import UUID

from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.names import check_shown
from apps.common.privacy import holds_bsn, mask_bsns
from apps.common.text import normalise
from apps.documents.models import DocumentSuggestion
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


def bsn_in_name() -> ApiError:
    return ApiError(
        422,
        "validation_error",
        _(
            "Een BSN bewaren we nooit, dus het kan niet in een naam staan. Post herkennen we "
            "aan de naam, niet aan het BSN. Laat het nummer weg."
        ),
    )


def _refuse_bsn(value: str, kept: Collection[str]) -> None:
    """A name is matched against letters, whose BSNs are all masked alike: a BSN (or the
    token it was masked to) would find every letter with any BSN. A value already stored
    on this person (masked before this was refused) may stay; it matches nothing."""
    if holds_bsn(value) and value.strip() not in kept:
        raise bsn_in_name()


def _tidy(value: str) -> str:
    """One form per name: Unicode NFC, whitespace runs one space, trimmed."""
    return unicodedata.normalize("NFC", " ".join(value.split()))


def _clean_name(display_name: str, kept: Collection[str] = ()) -> str:
    _refuse_bsn(display_name, kept)
    name = _tidy(mask_bsns(display_name))
    check_shown(name, empty=_("Een persoon heeft een naam nodig."))
    if len(name) > NAME_MAX:
        raise ApiError(
            422, "validation_error", _("Een naam is hoogstens %(max)d tekens.") % {"max": NAME_MAX}
        )
    return name


def _clean_variants(name_variants: list[str], kept: Collection[str] = ()) -> list[str]:
    """Tidied, masked and without repeats (as name matching compares); in the order given."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in name_variants:
        _refuse_bsn(raw, kept)
        variant = _tidy(mask_bsns(raw))
        check_shown(
            variant, empty=_("Een schrijfwijze van een naam heeft een zichtbaar teken nodig.")
        )
        if not VARIANT_MIN <= len(variant) <= VARIANT_MAX:
            raise ApiError(
                422,
                "validation_error",
                _("Een schrijfwijze van een naam is %(min)d tot %(max)d tekens.")
                % {"min": VARIANT_MIN, "max": VARIANT_MAX},
            )
        if normalise(variant) in seen:
            continue
        seen.add(normalise(variant))
        cleaned.append(variant)
    if len(cleaned) > VARIANTS_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Een persoon heeft hoogstens %(max)d schrijfwijzen.") % {"max": VARIANTS_MAX},
        )
    return cleaned


def _refuse_taken_names(person: Person) -> None:
    """Refuse a name of an active person that another active person has too.

    Names are compared as name matching compares them (case, accents and whitespace
    do not count): one name on two persons would propose both on the same letter.
    Archived persons are not matched, so their names do not count.
    """
    if person.archived_at is not None:
        return
    others = Person.objects.filter(workspace_id=person.workspace_id, archived_at__isnull=True)
    if person.pk is not None:
        others = others.exclude(pk=person.pk)
    display_names: dict[str, str] = {}
    taken: dict[str, str] = {}
    for other_name, other_variants in others.values_list("display_name", "name_variants"):
        display_names[normalise(other_name)] = other_name
        for name in (other_name, *other_variants):
            # A name masked before BSNs were refused matches nothing (common.text).
            if not holds_bsn(name):
                taken.setdefault(normalise(name), other_name)
    if normalise(person.display_name) in display_names:
        raise _person_exists()
    for name in (person.display_name, *person.name_variants):
        owner = None if holds_bsn(name) else taken.get(normalise(name))
        if owner is not None:
            raise ApiError(
                409,
                "name_variant_taken",
                _(
                    "De naam '%(name)s' hoort al bij %(person)s. Twee personen kunnen niet "
                    "dezelfde naam hebben."
                )
                % {"name": name, "person": owner},
            )


def _person_exists() -> ApiError:
    return ApiError(409, "person_exists", _("Er is al een persoon met deze naam."))


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
            raise _person_exists() from exc
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
        _refuse_taken_names(person)
        _save(person)
        return _annotated(workspace, person.id)


def update_person(
    *,
    workspace: Workspace,
    person_id: UUID,
    display_name: str,
    name_variants: list[str] | None,
    archived: bool | None,
) -> Person:
    """`name_variants` or `archived` None keeps the stored value."""
    with transaction.atomic():
        person = _get_for_update(workspace, person_id)
        kept = {person.display_name, *person.name_variants}
        person.display_name = _clean_name(display_name, kept)
        if name_variants is not None:
            person.name_variants = _clean_variants(name_variants, kept)
        if archived is False:
            person.archived_at = None
        elif archived and person.archived_at is None:
            person.archived_at = timezone.now()
            # An archived person cannot be added to a document, so their open proposals
            # could be neither accepted nor confirmed. Accepted and dismissed rows stay.
            DocumentSuggestion.objects.filter(
                person=person, state=DocumentSuggestion.State.OPEN
            ).delete()
        _refuse_taken_names(person)
        _save(person)
        return _annotated(workspace, person.id)


def delete_person(*, workspace: Workspace, person_id: UUID, actor: AuditActor) -> None:
    with transaction.atomic():
        # Rules first, in id order: a running rule holds its rules and then needs the person.
        rules = list(Rule.objects.select_for_update().filter(workspace=workspace).order_by("id"))
        person = _get_for_update(workspace, person_id)
        if _in_use(person):
            raise ApiError(
                409,
                "person_in_use",
                _("Deze persoon is nog in gebruik. Je kunt de persoon wel archiveren."),
            )
        for rule in rules:
            if person.id in named_ids(rule)[1]:
                raise ApiError(
                    409,
                    "person_in_use",
                    _("De regel '%(rule)s' gebruikt deze persoon. Pas die regel eerst aan.")
                    % {"rule": rule.name},
                )
        person.delete()
        record(
            AuditCode.PERSON_DELETED,
            actor=actor,
            target=AuditTarget(TargetKind.PERSON, person_id, person.display_name),
            workspace_id=workspace.id,
        )


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
        name = _tidy(mask_bsns(user.name))
        # Not linked to a person of the same name: a name does not prove who it is (an
        # admin links by hand, or invites with a person). The variant is left out when it
        # would hold a (masked) BSN, which matches every letter with one, or when another
        # active person has that name: both would be proposed, and neither could be
        # renamed or restored while the other has it.
        taken = {
            normalise(other)
            for other_name, variants in Person.objects.filter(
                workspace=workspace, archived_at__isnull=True
            ).values_list("display_name", "name_variants")
            for other in (other_name, *variants)
        }
        usable = (
            VARIANT_MIN <= len(name) <= VARIANT_MAX
            and not holds_bsn(name)
            and normalise(name) not in taken
        )
        return Person.objects.create(
            workspace=workspace,
            display_name=_free_name(workspace, name),
            name_variants=[name] if usable else [],
            user=user,
        )


def link_member(
    *, workspace: Workspace, person_id: UUID, user_id: UUID | None, actor: AuditActor
) -> Person:
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
        if person.user_id == user_id:
            # Nothing changes, so nothing is written to the log either.
            return _annotated(workspace, person.id)
        was_linked = person.user_id is not None
        person.user_id = user_id
        _save(person)
        if user_id is not None or was_linked:
            record(
                AuditCode.PERSON_LINKED if user_id is not None else AuditCode.PERSON_UNLINKED,
                actor=actor,
                target=AuditTarget(TargetKind.PERSON, person.id, person.display_name),
                workspace_id=workspace.id,
            )
        return _annotated(workspace, person.id)
