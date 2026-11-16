"""Managing, ordering, testing and running rules (design 9.5).

Callers hold the workspace's RLS context (see workspaces.deps).
"""

from uuid import UUID

from django.db import models, transaction
from django.db.models import Max, prefetch_related_objects
from django.utils.translation import gettext as _
from procrastinate.exceptions import AlreadyEnqueued

from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents.models import Document
from apps.documents.services import SUGGESTIONS, document_summary_out
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules import definition, jobs
from apps.rules.engine import Condition, evaluate
from apps.rules.facts import facts_for
from apps.rules.models import Rule, RuleRun
from apps.rules.schemas import RuleIn, RuleTestOut
from apps.workspaces.models import Workspace

RULES_MAX = 200
# "Test on existing documents" checks this many of the newest documents...
TEST_LIMIT = 500
# ...and shows this many of the matches.
TEST_SHOWN = 50


def _rules(workspace: Workspace) -> "models.QuerySet[Rule]":
    """The workspace's rules, each annotated with `applied_count` and `proposed_count`."""
    return Rule.objects.filter(workspace=workspace).annotate(
        applied_count=models.Count("runs", filter=models.Q(runs__outcome=RuleRun.Outcome.APPLIED)),
        proposed_count=models.Count(
            "runs", filter=models.Q(runs__outcome=RuleRun.Outcome.PROPOSED)
        ),
    )


def list_rules(workspace: Workspace) -> list[Rule]:
    """Rules in the order they run."""
    return list(_rules(workspace).order_by("position", "id"))


def rule_not_found() -> ApiError:
    return ApiError(404, "rule_not_found", _("We kunnen deze regel niet vinden."))


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)


def _get_for_update(workspace: Workspace, rule_id: UUID) -> Rule:
    rule = Rule.objects.select_for_update().filter(workspace=workspace, id=rule_id).first()
    if rule is None:
        raise rule_not_found()
    return rule


def _fill(rule: Rule, workspace: Workspace, data: RuleIn) -> None:
    """Validate `data` and copy it onto `rule` (not saved)."""
    name = mask_bsns(data.name).strip()
    if not name:
        raise _invalid(_("Een regel heeft een naam nodig."))
    if data.mode not in Rule.Mode.values:
        raise _invalid(_("Kies of de regel een voorstel doet of het zelf doet."))
    conditions = [Condition(field=c.field, op=c.op, value=c.value) for c in data.conditions]
    actions: list[definition.ActionIn] = [(a.type, a.label_id, a.person_id) for a in data.actions]
    definition.validate(
        data.match,
        conditions,
        actions,
        label_ids=set(Label.objects.filter(workspace=workspace).values_list("id", flat=True)),
        person_ids=set(
            Person.objects.filter(workspace=workspace, archived_at__isnull=True).values_list(
                "id", flat=True
            )
        ),
    )
    rule.name = name
    rule.active = data.active
    rule.mode = data.mode
    rule.stop = data.stop
    rule.definition = definition.to_json(data.match, conditions, actions)


def create_rule(*, workspace: Workspace, data: RuleIn) -> Rule:
    """Append a rule after the workspace's rules."""
    with transaction.atomic():
        # Two creates at once would both take the same position; the second waits here.
        Workspace.objects.select_for_update().filter(id=workspace.id).first()
        rules = Rule.objects.filter(workspace=workspace)
        if rules.count() >= RULES_MAX:
            raise _invalid(_("Je kunt hoogstens %(max)d regels maken.") % {"max": RULES_MAX})
        last = rules.aggregate(last=Max("position"))["last"]
        rule = Rule(workspace=workspace, position=(last or 0) + 1)
        _fill(rule, workspace, data)
        rule.save()
        return _rules(workspace).get(id=rule.id)


def update_rule(*, workspace: Workspace, rule_id: UUID, data: RuleIn) -> Rule:
    with transaction.atomic():
        rule = _get_for_update(workspace, rule_id)
        _fill(rule, workspace, data)
        rule.save()
        return _rules(workspace).get(id=rule.id)


def delete_rule(*, workspace: Workspace, rule_id: UUID) -> None:
    """Its runs go with it; history events keep the rule's name."""
    with transaction.atomic():
        _get_for_update(workspace, rule_id).delete()


def reorder_rules(*, workspace: Workspace, rule_ids: list[UUID]) -> list[Rule]:
    """Put the rules in this order; every rule of the workspace exactly once."""
    with transaction.atomic():
        rules = Rule.objects.select_for_update().filter(workspace=workspace).in_bulk()
        if len(rule_ids) != len(set(rule_ids)) or set(rule_ids) != set(rules):
            raise _invalid(_("Noem elke regel precies één keer."))
        for position, rule_id in enumerate(rule_ids, start=1):
            rules[rule_id].position = position
        # The position constraint is deferred, so swapping positions in one update is fine.
        Rule.objects.bulk_update(list(rules.values()), ["position"])
        return list_rules(workspace)


def test_rule(*, workspace: Workspace, data: RuleIn) -> RuleTestOut:
    """Which existing documents the (unsaved) rule's conditions hold for; stores nothing."""
    rule = Rule(workspace=workspace, position=0)
    _fill(rule, workspace, data)
    spec = definition.to_spec(rule)
    if spec is None:
        raise _invalid(_("Deze regel kunnen we niet lezen."))
    documents = Document.objects.filter(workspace=workspace).exclude(
        status=Document.Status.PROCESSING
    )
    total = documents.count()
    checked = list(documents.order_by("-created_at", "-id")[:TEST_LIMIT])
    matched = [d for d in checked if spec.id in evaluate(facts_for(d), [spec]).matched_rule_ids]
    shown = matched[:TEST_SHOWN]
    prefetch_related_objects(shown, "addressee_links", "label_links", SUGGESTIONS)
    return RuleTestOut(
        total_count=total,
        checked_count=len(checked),
        matched_count=len(matched),
        documents=[document_summary_out(d) for d in shown],
    )


def run_rule(*, workspace: Workspace, rule_id: UUID) -> None:
    """Queue applying the rule to the workspace's existing documents."""
    with transaction.atomic():
        rule = Rule.objects.filter(workspace=workspace, id=rule_id).first()
        if rule is None:
            raise rule_not_found()
        if not rule.active:
            raise ApiError(409, "rule_inactive", _("Deze regel staat uit. Zet hem eerst aan."))
        # One waiting run per rule: a second click while the first is queued is the same ask.
        # The savepoint keeps the refused insert from aborting the request's transaction.
        try:
            with transaction.atomic():
                jobs.apply_rule_to_existing.configure(queueing_lock=f"rule-{rule.id}").defer(
                    workspace_id=str(workspace.id), rule_id=str(rule.id)
                )
        except AlreadyEnqueued:
            pass
