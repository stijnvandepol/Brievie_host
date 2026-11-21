"""The AI's proposal of a person nobody knows yet (Mensen, Besluit B3).

Only a proposal: nothing automatic makes a person (spec M7). The pipeline proposes; a
member accepts (the person is made by `people.services.create_person`, as by hand, and
put on the letter through `organise`) or dismisses (the letter's memory of "no": the name
is never proposed on it again). Callers hold the workspace's RLS context; the letter is
locked by whoever changes it.
"""

from collections.abc import Sequence
from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.common.privacy import holds_bsn, mask_bsns
from apps.common.text import normalise
from apps.documents.models import Document, DocumentEvent, NewPersonSuggestion
from apps.people.models import Person
from apps.pipeline.validation import ValidatedNewName

State = NewPersonSuggestion.State


def known_name_keys(workspace_id: UUID) -> set[str]:
    """Every name and variant of every person and department of the workspace, archived
    ones included (a name taken by any of them cannot be given to a new person), as
    `normalise` keys."""
    keys: set[str] = set()
    for name, variants in Person.objects.filter(workspace_id=workspace_id).values_list(
        "display_name", "name_variants"
    ):
        for value in (name, *variants):
            if not holds_bsn(value):
                keys.add(normalise(value))
    return keys


def propose(document: Document, names: Sequence[ValidatedNewName]) -> int:
    """Record the AI's unknown names on the letter; the open ones of an earlier run go
    first. A name somebody has (any person, archived ones too), one that was dismissed or
    accepted on this letter, is not proposed. Returns how many are open now. The caller
    holds the letter's row lock."""
    with transaction.atomic():
        NewPersonSuggestion.objects.filter(document=document, state=State.OPEN).delete()
        if not names:
            return 0
        taken = known_name_keys(document.workspace_id)
        decided = set(
            NewPersonSuggestion.objects.filter(document=document).values_list("name_key", flat=True)
        )
        made = 0
        for name in names:
            if name.key in taken or name.key in decided:
                continue
            NewPersonSuggestion.objects.create(
                workspace_id=document.workspace_id,
                document=document,
                name=mask_bsns(name.name),
                name_key=name.key,
                quote=mask_bsns(name.quote),
                confidence=name.confidence,
            )
            decided.add(name.key)
            made += 1
        return made


def has_open(document: Document) -> bool:
    """Whether the letter has an open proposal of a new person: whose letter it is, is
    then still a question (no automatic confirm)."""
    return NewPersonSuggestion.objects.filter(document=document, state=State.OPEN).exists()


def open_for(document: Document) -> list[NewPersonSuggestion]:
    return list(
        NewPersonSuggestion.objects.filter(document=document, state=State.OPEN).order_by(
            "-confidence", "name_key"
        )
    )


def _locked(document: Document, suggestion_id: UUID) -> NewPersonSuggestion:
    row = (
        NewPersonSuggestion.objects.select_for_update()
        .filter(document=document, id=suggestion_id)
        .first()
    )
    if row is None:
        raise ApiError(404, "suggestion_not_found", _("We kunnen dit voorstel niet vinden."))
    return row


def accept(
    document: Document,
    suggestion_id: UUID,
    *,
    actor: AuditActor,
    actor_role: str,
) -> None:
    """A member says yes: the person is made as by hand (`create_person`: its rules, the
    BSN refusal, the log) and put on the letter. When a person with this name exists by
    now, that person is put on the letter instead. Accepting again changes nothing. The
    caller locked the letter for organising (`require_editable` included)."""
    from apps.documents import organise, visibility
    from apps.people.services import create_person

    with transaction.atomic():
        row = _locked(document, suggestion_id)
        if row.state == State.ACCEPTED:
            return
        if row.state == State.DISMISSED:
            raise ApiError(404, "suggestion_not_found", _("We kunnen dit voorstel niet vinden."))
        existing = next(
            (
                person
                for person in Person.objects.filter(
                    workspace_id=document.workspace_id, archived_at__isnull=True
                )
                if normalise(person.display_name) == row.name_key
                or any(normalise(v) == row.name_key for v in person.name_variants)
            ),
            None,
        )
        person = existing or create_person(
            workspace=document.workspace,
            display_name=row.name,
            name_variants=[],
            actor_role=actor_role,
            actor=actor,
        )
        user = None
        if actor.user_id is not None:
            from apps.accounts.models import User

            user = User.objects.get(id=actor.user_id)
        with visibility.addressee_scope(document):
            organise.add_addressee(
                document, person, actor=user, origin=DocumentEvent.Origin.SUGGESTION
            )
        row.state = State.ACCEPTED
        row.person = person
        row.save(update_fields=["state", "person", "updated_at"])


def dismiss(document: Document, suggestion_id: UUID) -> None:
    """A member says no: the name is not proposed on this letter again."""
    with transaction.atomic():
        row = _locked(document, suggestion_id)
        if row.state == State.OPEN:
            row.state = State.DISMISSED
            row.save(update_fields=["state", "updated_at"])
