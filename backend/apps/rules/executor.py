"""Carry out what the engine planned for one document (design 9.4).

The caller holds the workspace's RLS context and the document's row lock
(`select_for_update`) for the whole transaction, as `suggestions.propose` needs.
"""

from decimal import Decimal
from uuid import UUID

from django.db import transaction

from apps.documents import organise, suggestions
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.organise import ADDRESSEES_MAX, LABELS_MAX
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules.engine import Evaluation, PlannedAction
from apps.rules.models import Rule, RuleRun

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
_ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE

# A rule's proposal is certain: its conditions held.
RULE_CONFIDENCE = Decimal("1")


def execute(document: Document, evaluation: Evaluation, *, trigger: str) -> None:
    """Apply the `auto` actions and record the `propose` ones; one `RuleRun` per rule
    that changed or proposed something.

    A target that no longer exists or is archived is skipped, and so is a change the
    user dismissed on this document. When one target is both proposed and applied in
    the same evaluation, it is only applied. A change that would take the document
    over its limit of labels or addressees (applied, or once accepted) is skipped too.
    """
    planned = evaluation.planned
    if not planned:
        return
    with transaction.atomic():
        # Locked: a rule deleted meanwhile is skipped here, and cannot be deleted before
        # its run and events are written (that would break the commit). `no_key` blocks a
        # delete but not another run's foreign-key checks; id order avoids a deadlock
        # between two runs.
        rules = {
            rule.id: rule
            for rule in Rule.objects.select_for_update(no_key=True)
            .filter(workspace_id=document.workspace_id, id__in={p.rule_id for p in planned})
            .order_by("id")
        }
        # Filtered by workspace as well as by RLS: a stored id may name another workspace's row.
        workspace_id = document.workspace_id
        labels = Label.objects.filter(workspace_id=workspace_id).in_bulk(
            [p.target_id for p in planned if p.action == _ADD_LABEL]
        )
        persons = Person.objects.filter(workspace_id=workspace_id).in_bulk(
            [p.target_id for p in planned if p.action != _ADD_LABEL]
        )
        applied = {(p.action, p.target_id) for p in planned if p.mode == Rule.Mode.AUTO}
        room: dict[str, int] = {
            _ADD_LABEL: LABELS_MAX - DocumentLabel.objects.filter(document=document).count(),
            _ADD_ADDRESSEE: ADDRESSEES_MAX
            - DocumentAddressee.objects.filter(document=document).count(),
        }
        outcomes: dict[UUID, str] = {}
        for action in planned:
            rule = rules.get(action.rule_id)
            target = _target(action, labels, persons)
            if rule is None or target is None or room[action.action] <= 0:
                continue
            if suggestions.is_dismissed(document, action.action, action.target_id):
                continue
            if action.mode == Rule.Mode.AUTO:
                if _apply(document, action, target, rule):
                    room[action.action] -= 1
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
    """Whether the proposal is new: a new open row, or an open row this rule took over.

    Re-proposing what the rule already proposed is no new proposal, so it adds no run.
    """
    before = suggestions.open_row(document, action.action, action.target_id)
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
    if row is None:
        return False
    return before is None or (before.origin, before.rule_id) != (row.origin, row.rule_id)
