"""Carry out what the engine planned for one document (design 9.4).

The caller holds the workspace's RLS context and the document's row lock
(`select_for_update`) for the whole transaction, as `suggestions.propose` needs.
"""

from decimal import Decimal
from uuid import UUID

from django.db import transaction

from apps.documents import organise, suggestions
from apps.documents.models import Document, DocumentEvent, DocumentSuggestion
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules.engine import Evaluation, PlannedAction
from apps.rules.models import Rule, RuleRun

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL

# A rule's proposal is certain: its conditions held.
RULE_CONFIDENCE = Decimal("1")


def execute(document: Document, evaluation: Evaluation, *, trigger: str) -> None:
    """Apply the `auto` actions and record the `propose` ones; one `RuleRun` per rule
    that changed or proposed something.

    A target that no longer exists or is archived is skipped, and so is a change the
    user dismissed on this document. When one target is both proposed and applied in
    the same evaluation, it is only applied.
    """
    planned = evaluation.planned
    if not planned:
        return
    with transaction.atomic():
        rules = Rule.objects.in_bulk({p.rule_id for p in planned})
        labels = Label.objects.in_bulk([p.target_id for p in planned if p.action == _ADD_LABEL])
        persons = Person.objects.in_bulk([p.target_id for p in planned if p.action != _ADD_LABEL])
        applied = {(p.action, p.target_id) for p in planned if p.mode == Rule.Mode.AUTO}
        outcomes: dict[UUID, str] = {}
        for action in planned:
            rule = rules.get(action.rule_id)
            target = _target(action, labels, persons)
            if rule is None or target is None:
                continue
            if suggestions.is_dismissed(document, action.action, action.target_id):
                continue
            if action.mode == Rule.Mode.AUTO:
                if _apply(document, action, target, rule):
                    outcomes[rule.id] = RuleRun.Outcome.APPLIED
            elif (action.action, action.target_id) not in applied and _propose(
                document, action, target, rule
            ):
                outcomes.setdefault(rule.id, RuleRun.Outcome.PROPOSED)
        for rule_id, outcome in outcomes.items():
            RuleRun.objects.create(
                workspace_id=document.workspace_id,
                rule=rules[rule_id],
                document=document,
                trigger=trigger,
                outcome=outcome,
            )


def _target(
    action: PlannedAction, labels: dict[UUID, Label], persons: dict[UUID, Person]
) -> Label | Person | None:
    if action.action == _ADD_LABEL:
        return labels.get(action.target_id)
    person = persons.get(action.target_id)
    if person is None or person.archived_at is not None:
        return None
    return person


def _apply(document: Document, action: PlannedAction, target: Label | Person, rule: Rule) -> bool:
    origin = DocumentEvent.Origin.RULE
    if isinstance(target, Label):
        added = organise.add_label(document, target, actor=None, origin=origin, rule=rule)
    else:
        added = organise.add_addressee(document, target, actor=None, origin=origin, rule=rule)
    if added:
        suggestions.mark_accepted(document, action.action, action.target_id)
    return added


def _propose(document: Document, action: PlannedAction, target: Label | Person, rule: Rule) -> bool:
    row = suggestions.propose(
        document,
        action=action.action,
        label=target if isinstance(target, Label) else None,
        person=target if isinstance(target, Person) else None,
        origin=DocumentSuggestion.Origin.RULE,
        quote=action.quote,
        confidence=RULE_CONFIDENCE,
        rule_name=action.rule_name,
        rule=rule,
    )
    return row is not None
