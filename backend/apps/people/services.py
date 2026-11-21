import unicodedata
from collections.abc import Collection
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import IntegrityError, connection, models, transaction
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.audit.codes import ActorKind, AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.names import check_shown
from apps.common.privacy import holds_bsn, mask_bsns
from apps.common.text import normalise
from apps.documents import visibility
from apps.documents.member_export import remove_member_exports
from apps.documents.models import DocumentSuggestion
from apps.people.models import DepartmentMember, Person
from apps.rules.definition import named_ids
from apps.rules.models import Rule
from apps.tasks.assignment import release_unseeing_assignees
from apps.workspaces.context import system_actor
from apps.workspaces.locks import lock_memberships
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_rules import department_category, person_category
from apps.workspaces.visibility_settings import (
    MemberChange,
    member_change_key,
    notify_self_grant,
    sight_change,
)

if TYPE_CHECKING:
    from apps.people.accounts import PersonAccount

_NAME_UNIQUE = "people_person_name_unique"
_USER_UNIQUE = "people_person_user_unique"

NAME_MAX = 150
VARIANTS_MAX = 20
VARIANT_MIN = 2
VARIANT_MAX = 100


def _persons(workspace: Workspace) -> "models.QuerySet[Person]":
    """The workspace's persons, each annotated with `document_count`, with the members
    of a department (`PersonOut.member_user_ids`, `lead_user_ids`)."""
    return (
        Person.objects.filter(workspace=workspace)
        .annotate(document_count=models.Count("document_links"))
        .prefetch_related(DEPARTMENT_MEMBERS)
    )


# A department's members in a stable order, fetched once for a list of persons.
DEPARTMENT_MEMBERS = models.Prefetch(
    "department_members", queryset=DepartmentMember.objects.order_by("created_at", "id")
)


def _in_use(person: Person) -> bool:
    """Whether deleting would lose information; archiving is the alternative.

    Counts letters the caller cannot see too: the caller runs it as the system.
    """
    return person.user_id is not None or person.document_links.exists()


def _trashed_letter_ids(person: Person) -> set[UUID] | None:
    """The letters in the trash that keep the person in use, when only such letters do
    (no account, no link to a live letter): after the purge it can go. None otherwise.
    As the system, like `_in_use`."""
    if person.user_id is not None:
        return None
    if person.document_links.filter(document__trashed_at__isnull=True).exists():
        return None
    return set(person.document_links.values_list("document_id", flat=True))


def _all_in_own_trash(viewer: Membership, document_ids: set[UUID]) -> bool:
    """Whether the caller sees every one of these letters in their own trash (never one
    they may not read, nor one whose purge started); as the caller."""
    return bool(document_ids) and visibility.trashed_documents(viewer).filter(
        id__in=document_ids
    ).count() == len(document_ids)


def list_persons(workspace: Workspace) -> list[Person]:
    """Active persons by name, then archived ones; needs the workspace's RLS context.

    `document_count` from one grouped count over the letters the policy shows, not a
    link-by-link join (measured, DECISIONS 2026-11-20). The same number: a link is
    visible exactly when its letter is, and `addressee_ids` mirrors the links. A letter in
    the trash is not counted.
    """
    persons = list(
        Person.objects.filter(workspace=workspace)
        .prefetch_related(DEPARTMENT_MEMBERS)
        .order_by(
            models.ExpressionWrapper(
                models.Q(archived_at__isnull=False), output_field=models.BooleanField()
            ).asc(),
            Lower("display_name"),
            "id",
        )
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.id, pg_catalog.count(*) FROM public.documents_document d,"
            " LATERAL pg_catalog.unnest(d.addressee_ids) AS a(id)"
            " WHERE d.workspace_id = %s AND d.trashed_at IS NULL GROUP BY a.id",
            [workspace.id],
        )
        counts: dict[UUID, int] = {row[0]: int(row[1]) for row in cursor.fetchall()}
    for person in persons:
        person.document_count = counts.get(person.id, 0)  # type: ignore[attr-defined]
    return persons


def _get_for_update(workspace: Workspace, person_id: UUID) -> Person:
    person = (
        Person.objects.select_for_update(no_key=True)
        .filter(workspace=workspace, id=person_id)
        .first()
    )
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


# At most this many persons per workspace, made by hand (security review 2026-11-20):
# every letter's name matching walks them all on the shared worker, and the AI gets only
# the first 50, so a flood of persons pushes the real ones out of its list.
MAX_PERSONS = 500


# `actor_role` of a call the system makes on nobody's behalf (a command, a test set-up):
# stated by the caller, never a default, so a request that forgets its role is refused.
ACTING_AS_SYSTEM = "system"


def _acts_as_admin(actor_role: str) -> bool:
    """Every permission check here asks this; `actor_role` has no default anywhere."""
    return actor_role in (Membership.Role.ADMIN, ACTING_AS_SYSTEM)


def _valid_kind(kind: str) -> str:
    if kind not in Person.Kind.values:
        raise ApiError(422, "validation_error", _("Kies een persoon of een afdeling."))
    return kind


def create_person(
    *,
    workspace: Workspace,
    display_name: str,
    name_variants: list[str],
    archived: bool = False,
    kind: str = Person.Kind.PERSON,
    actor_role: str,
    actor: AuditActor,
) -> Person:
    """A person, or a department (`kind`; only an admin makes one: departments decide
    who reads post). The kind is set here only: an update never changes it. Recorded
    (`person_created`): under Mensen every person is an account slot (spec 6). Only a
    human's request reaches this; nothing automatic makes a person (spec M7)."""
    _valid_kind(kind)
    if kind == Person.Kind.DEPARTMENT and not _acts_as_admin(actor_role):
        raise _forbidden()
    with transaction.atomic():
        if Person.objects.filter(workspace=workspace).count() >= MAX_PERSONS:
            raise ApiError(
                409,
                "too_many_persons",
                _("Deze werkruimte heeft het grootste aantal personen. Verwijder er eerst een."),
            )
        person = Person(
            workspace=workspace,
            display_name=_clean_name(display_name),
            name_variants=_clean_variants(name_variants),
            archived_at=timezone.now() if archived else None,
            kind=kind,
        )
        _refuse_taken_names(person)
        _save(person)
        record(
            AuditCode.PERSON_CREATED,
            actor=actor,
            target=AuditTarget(TargetKind.PERSON, person.id, person.display_name),
            workspace_id=workspace.id,
        )
        return _annotated(workspace, person.id)


def update_person(
    *,
    workspace: Workspace,
    person_id: UUID,
    display_name: str,
    name_variants: list[str] | None,
    archived: bool | None,
    actor_role: str,
    actor: AuditActor,
) -> Person:
    """`name_variants` or `archived` None keeps the stored value.

    Archiving stops the person's open invitations (`invitation_revoked`, reason
    `person_archived`): an archived person cannot be invited, so an old link must not
    activate them. Those invitations are locked before the person (lock order:
    memberships, invitations, person).

    A department, and a person linked to an account, are changed by admins only (403
    `forbidden`): their names decide through name matching whose post a letter becomes,
    so a member renaming or adding a variant could steer letters to themselves or to
    another member (review of PR #38). Members keep editing persons without an account."""
    from apps.workspaces.invitations import (
        lock_open_invitations_for_person,
        revoke_invitations_for_person,
    )

    with transaction.atomic():
        invitations = (
            lock_open_invitations_for_person(workspace_id=workspace.id, person_id=person_id)
            if archived
            else []
        )
        person = _get_for_update(workspace, person_id)
        if not _acts_as_admin(actor_role) and (
            person.kind == Person.Kind.DEPARTMENT or person.user_id is not None
        ):
            raise _forbidden()
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
            # As the system: proposals on letters the caller cannot see go too.
            with system_actor():
                DocumentSuggestion.objects.filter(
                    person=person, state=DocumentSuggestion.State.OPEN
                ).delete()
            revoke_invitations_for_person(invitations, actor=actor)
        _refuse_taken_names(person)
        _save(person)
        return _annotated(workspace, person.id)


def delete_person(
    *,
    workspace: Workspace,
    person_id: UUID,
    actor: AuditActor,
    viewer: Membership | None = None,
) -> None:
    """Delete a person nothing uses. The refusal names the trash only when `viewer` (the
    caller) sees each letter that keeps the person in their own trash; otherwise it says
    "in use", so it never tells of a letter the caller may not see."""
    with transaction.atomic():
        # Rules first, in id order: a running rule holds its rules and then needs the person.
        rules = list(Rule.objects.select_for_update().filter(workspace=workspace).order_by("id"))
        person = _get_for_update(workspace, person_id)
        # The caller is authorised above; the use and the delete reach letters the caller
        # cannot see (a hidden link would otherwise fail the commit). The message never
        # says what the use is.
        with system_actor():
            in_use = _in_use(person)
            in_trash = _trashed_letter_ids(person) if in_use else None
        if viewer is not None and in_trash is not None and _all_in_own_trash(viewer, in_trash):
            raise ApiError(
                409,
                "person_in_use",
                _(
                    "Deze persoon staat nog op een brief in de prullenbak. Na het wissen van "
                    "die brief kun je de persoon verwijderen. Archiveren kan nu al."
                ),
            )
        if in_use:
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
        with system_actor():
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


def _lock_for_sight(
    workspace: Workspace, actor: AuditActor, *, actor_role: str
) -> tuple[list[Membership], str]:
    """Every membership of the workspace (the lock of every change of who sees what,
    S 4.4) and the acting member's role as the locked rows have it: never the role an
    endpoint read before the lock (a demotion in between counts). 404
    `workspace_not_found` when the acting member is gone. A call without a user is the
    system only when it says so (`ACTING_AS_SYSTEM` and a system actor); else it acts
    with no rights."""
    locked = lock_memberships(workspace)
    if actor.user_id is None:
        system = actor_role == ACTING_AS_SYSTEM and actor.kind == ActorKind.SYSTEM
        return locked, ACTING_AS_SYSTEM if system else ""
    own = next((m for m in locked if m.user_id == actor.user_id), None)
    if own is None:
        raise ApiError(404, "workspace_not_found", _("We kunnen deze werkruimte niet vinden."))
    return locked, own.role


def link_member(
    *,
    workspace: Workspace,
    person_id: UUID,
    user_id: UUID | None,
    actor: AuditActor,
    actor_role: str,
    confirmed: bool = False,
    preview_token: str = "",
) -> Person:
    """Link the person to a member of the workspace, or unlink it (`user_id` None): a grant
    of the person's post (PB T12, D 8.1).

    Linking needs an admin (403 `forbidden`), never to the admin's own account when the
    person has private post (409 `cannot_grant_self`). A person linked to somebody is
    unlinked or relinked only by that member (409 `cannot_unlink`; removing the member
    unlinks too); unlinking oneself is deliberate and allowed, also for a viewer. The
    system (no user) is not bound by that. A link by which somebody gains sight needs the
    confirmed preview (409 `confirmation_required`, `preview_outdated`; S 4.4); an unlink
    sends the person's post to the sorters without one (P6, P7). An admin linking
    themselves: the other admins are told (S 4.7). A member's rights are those of the
    locked membership; `actor_role` counts only for a call without a user, which must
    say `ACTING_AS_SYSTEM`. Lock order: every membership, then the person."""
    with transaction.atomic():
        locked, locked_role = _lock_for_sight(workspace, actor, actor_role=actor_role)
        person = _get_for_update(workspace, person_id)
        if person.kind == Person.Kind.DEPARTMENT:
            raise ApiError(
                409,
                "is_a_department",
                _("Een afdeling koppel je niet aan één account. Voeg leden toe."),
            )
        if user_id is not None and not _acts_as_admin(locked_role):
            raise _forbidden()
        if person.user_id == user_id:
            # Nothing changes, so nothing is written to the log either.
            return _annotated(workspace, person.id)
        if user_id is not None:
            if not Membership.objects.filter(workspace=workspace, user_id=user_id).exists():
                raise ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))
            if (
                Person.objects.filter(workspace=workspace, user_id=user_id)
                .exclude(id=person.id)
                .exists()
            ):
                raise _member_already_linked()
        if (
            person.user_id is not None
            and actor.user_id is not None
            and person.user_id != actor.user_id
        ):
            linked = User.objects.filter(id=person.user_id).values_list("name", flat=True).first()
            raise ApiError(
                409,
                "cannot_unlink",
                _("Alleen %(name)s kan deze koppeling losmaken.") % {"name": linked or ""},
            )
        if user_id is not None and user_id == actor.user_id and visibility.has_private_post(person):
            raise _cannot_grant_self()
        former = person.user_id
        former_stays_member = former is not None and any(m.user_id == former for m in locked)
        with sight_change(
            workspace,
            narrowing=user_id is None,
            confirmed=confirmed,
            preview_token=preview_token,
            actor=actor,
            change=member_change_key(
                MemberChange(kind="link", user_id=user_id, person_id=person.id)
                if user_id is not None
                else MemberChange(kind="unlink", person_id=person.id)
            ),
        ) as sight:
            person.user_id = user_id
            if user_id is not None:
                # Linked: an account again, no longer "uitgeschakeld".
                person.deactivated_at = None
                person.former_user = None
            _save(person)
            visibility.recompute_where(workspace_id=workspace.id, person_id=person.id)
            if former is not None:
                # The former member may no longer read this person's post: their
                # assignments there end (Mijn taken S 5.3), and so does their own export,
                # which held it.
                release_unseeing_assignees(workspace_id=workspace.id)
                remove_member_exports(workspace.id, former)
            if user_id is not None:
                visibility.record_grant(
                    person, grantee=User.objects.get(id=user_id), actor_user_id=actor.user_id
                )
            record(
                AuditCode.PERSON_LINKED if user_id is not None else AuditCode.PERSON_UNLINKED,
                actor=actor,
                target=AuditTarget(TargetKind.PERSON, person.id, person.display_name),
                workspace_id=workspace.id,
            )
        if former is not None and former_stays_member:
            # Every member keeps exactly one person (spec M8): the former member gets a
            # fresh one at once, after the change of sight is settled. A new person is on
            # no letter, so nobody's sight changes by it (and nobody is told of a "gain"
            # of an empty name); the old person stays, "niet actief".
            ensure_person_for_member(workspace=workspace, user=User.objects.get(id=former))
        if user_id is not None and user_id == actor.user_id:
            others = [
                m.user for m in locked if m.role == Membership.Role.ADMIN and m.user_id != user_id
            ]
            notify_self_grant(
                others,
                category=person_category(person.id),
                names=sight.before,
                actor_name=actor.name,
            )
        return _annotated(workspace, person.id)


def _forbidden() -> ApiError:
    return ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))


def _department_for_update(workspace: Workspace, person_id: UUID, actor: AuditActor) -> Person:
    """The department, locked after every membership (the lock order of a change of
    sight), for an admin by the locked rows (403 `forbidden`) or the system; 409
    `not_a_department` for a person."""
    _locked, role = _lock_for_sight(workspace, actor, actor_role=ACTING_AS_SYSTEM)
    if not _acts_as_admin(role):
        raise _forbidden()
    department = _get_for_update(workspace, person_id)
    if department.kind != Person.Kind.DEPARTMENT:
        raise ApiError(409, "not_a_department", _("Dit is geen afdeling."))
    return department


def _member_name(workspace: Workspace, user_id: UUID) -> str:
    """The member's name, or 404 `member_not_found` for somebody who is no member."""
    name = (
        Membership.objects.filter(workspace=workspace, user_id=user_id)
        .values_list("user__name", flat=True)
        .first()
    )
    if name is None:
        raise ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))
    return str(name)


def _department_target(department: Person) -> AuditTarget:
    return AuditTarget(TargetKind.PERSON, department.id, department.display_name)


def add_department_member(
    *,
    workspace: Workspace,
    person_id: UUID,
    user_id: UUID,
    actor: AuditActor,
    is_lead: bool = False,
    confirmed: bool = False,
    preview_token: str = "",
) -> Person:
    """Put a member in a department (S 4.5, PB T11): a grant, so with departments on and
    "Iedereen ziet alle post" off it needs the confirmed preview (409
    `confirmation_required`, `preview_outdated`). An admin never adds themselves to a
    department with private post (409 `cannot_grant_self`); one who adds themselves
    elsewhere: the department's members are told (S 4.7). Adding a member who is in it
    already changes nothing."""
    with transaction.atomic():
        department = _department_for_update(workspace, person_id, actor)
        name = _member_name(workspace, user_id)
        if DepartmentMember.objects.filter(department=department, user_id=user_id).exists():
            return _annotated(workspace, department.id)
        if user_id == actor.user_id and visibility.has_private_post(department):
            raise _cannot_grant_self()
        colleagues = list(
            User.objects.filter(
                id__in=DepartmentMember.objects.filter(department=department).values("user_id")
            )
        )
        with sight_change(
            workspace,
            narrowing=False,
            confirmed=confirmed,
            preview_token=preview_token,
            actor=actor,
            change=member_change_key(
                MemberChange(kind="department_add", user_id=user_id, person_id=department.id)
            ),
        ) as sight:
            DepartmentMember.objects.create(
                workspace=workspace, department=department, user_id=user_id, is_lead=is_lead
            )
            visibility.recompute_where(workspace_id=workspace.id, person_id=department.id)
            grantee = User.objects.get(id=user_id)
            visibility.record_grant(department, grantee=grantee, actor_user_id=actor.user_id)
            record(
                AuditCode.DEPARTMENT_MEMBER_ADDED,
                actor=actor,
                target=_department_target(department),
                workspace_id=workspace.id,
                values={"member": name},
            )
        if user_id == actor.user_id:
            notify_self_grant(
                colleagues,
                category=department_category(department.id),
                names=sight.before,
                actor_name=actor.name,
            )
        return _annotated(workspace, department.id)


def remove_department_member(
    *, workspace: Workspace, person_id: UUID, user_id: UUID, actor: AuditActor
) -> Person:
    """Take a member out of a department; their sight of its post goes (also for an admin
    removing themselves: deliberate, so no `would_lose_access`, ruling c). The last member
    leaving sends the department's post to the sorters (P7), without a confirmation (P6)."""
    with transaction.atomic():
        department = _department_for_update(workspace, person_id, actor)
        name = _member_name(workspace, user_id)
        if not DepartmentMember.objects.filter(department=department, user_id=user_id).exists():
            return _annotated(workspace, department.id)
        with sight_change(
            workspace,
            narrowing=True,
            confirmed=False,
            preview_token="",
            actor=actor,
            change=member_change_key(
                MemberChange(kind="department_remove", user_id=user_id, person_id=department.id)
            ),
        ):
            DepartmentMember.objects.filter(department=department, user_id=user_id).delete()
            visibility.recompute_where(workspace_id=workspace.id, person_id=department.id)
            # The member may no longer read the department's post: their tasks there end,
            # and so does their own export (it held the department's private letters).
            release_unseeing_assignees(workspace_id=workspace.id)
            remove_member_exports(workspace.id, user_id)
            record(
                AuditCode.DEPARTMENT_MEMBER_REMOVED,
                actor=actor,
                target=_department_target(department),
                workspace_id=workspace.id,
                values={"member": name},
            )
        return _annotated(workspace, department.id)


def update_department_member(
    *, workspace: Workspace, person_id: UUID, user_id: UUID, is_lead: bool, actor: AuditActor
) -> Person:
    """Make a department member its lead ("leidinggevende") or not. A lead manages the
    department's labels and sees nothing extra (S 4.5). 404 `member_not_found` for
    somebody who is not in the department."""
    with transaction.atomic():
        department = _department_for_update(workspace, person_id, actor)
        row = (
            DepartmentMember.objects.select_for_update()
            .filter(department=department, user_id=user_id)
            .select_related("user")
            .first()
        )
        if row is None:
            raise ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))
        if row.is_lead != is_lead:
            row.is_lead = is_lead
            row.save(update_fields=["is_lead", "updated_at"])
            record(
                AuditCode.DEPARTMENT_LEAD_CHANGED,
                actor=actor,
                target=_department_target(department),
                workspace_id=workspace.id,
                values={"member": row.user.name, "value": is_lead},
            )
        return _annotated(workspace, department.id)


def _cannot_grant_self() -> ApiError:
    return ApiError(
        409,
        "cannot_grant_self",
        _(
            "Je kunt jezelf geen toegang geven tot privé-post. Gebruik een uitnodiging of "
            "noodtoegang."
        ),
    )


def open_emergency_access(
    *, workspace: Workspace, person_id: UUID, reason: str, actor: AuditActor
) -> visibility.EmergencyResult:
    """Emergency access to the post of one person or department (S 4.7): 404
    `person_not_found`, else `documents.visibility.open_emergency`."""
    person = Person.objects.filter(workspace=workspace, id=person_id).first()
    if person is None:
        raise ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
    return visibility.open_emergency(workspace=workspace, person=person, actor=actor, reason=reason)


def list_people(
    workspace: Workspace, *, viewer: Membership, state: str = ""
) -> "list[PersonAccount]":
    """Mensen: every person with their account state (`apps.people.accounts`)."""
    from apps.people import accounts

    return accounts.list_people(workspace, viewer=viewer, state=state)


def person_has_private_post(workspace: Workspace, person_id: UUID) -> bool:
    """Whether a private letter is on the person's name (`apps.people.accounts`)."""
    from apps.people import accounts

    return accounts.person_has_private_post(workspace, person_id)
