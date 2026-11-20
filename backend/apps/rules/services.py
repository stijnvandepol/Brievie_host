"""Managing, ordering, testing and running rules (design 9.5).

Callers hold the workspace's RLS context (see workspaces.deps).
"""

from uuid import UUID

from django.db import models, transaction
from django.db.models import F, Max, Sum, Value, Window, prefetch_related_objects
from django.db.models.functions import Least, Left, Length
from django.utils.translation import gettext as _

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents.models import Document
from apps.documents.services import HEADLINE_PROPOSALS, SUGGESTIONS, document_summary_out
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules import definition, jobs
from apps.rules.engine import Condition, evaluate
from apps.rules.facts import facts_for_many
from apps.rules.models import Rule, RuleRun
from apps.rules.schemas import RuleIn, RuleTestOut
from apps.rules.templates import RuleTemplate, rule_templates
from apps.workspaces.context import system_actor
from apps.workspaces.models import Workspace

RULES_MAX = 200
# "Test on existing documents" checks this many of the newest documents...
TEST_LIMIT = 500
# ...and shows this many of the matches.
TEST_SHOWN = 50
# The test runs inside the request, and normalising text costs about 0.3 s per million
# characters, so it reads at most this many characters of each document (a letter can be
# 600,000)...
TEST_TEXT_CHARS = 100_000
# ...and at most this many characters in all; it stops before the document that would go
# over, and `checked_count` says how many it got to.
TEST_TEXT_BUDGET = 2_000_000


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


def list_templates() -> tuple[RuleTemplate, ...]:
    """The example rules of the gallery; the same for every workspace."""
    return rule_templates()


def rule_not_found() -> ApiError:
    return ApiError(404, "rule_not_found", _("We kunnen deze regel niet vinden."))


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)


def _get_for_update(workspace: Workspace, rule_id: UUID) -> Rule:
    rule = Rule.objects.select_for_update().filter(workspace=workspace, id=rule_id).first()
    if rule is None:
        raise rule_not_found()
    return rule


def _fill(rule: Rule, workspace: Workspace, data: RuleIn, *, any_person: bool = False) -> None:
    """Validate `data` and copy it onto `rule` (not saved).

    A rule may name a person who is not archived, and keeps naming an archived one it
    already named: it can always be switched off or edited (the executor skips an
    archived target, and the web shows the person as archived). `any_person` allows
    every person, for a test that stores nothing.
    """
    name = mask_bsns(data.name).strip()
    if not name:
        raise _invalid(_("Een regel heeft een naam nodig."))
    if data.mode not in Rule.Mode.values:
        raise _invalid(_("Kies of de regel een voorstel doet of het zelf doet."))
    conditions = [Condition(field=c.field, op=c.op, value=c.value) for c in data.conditions]
    actions: list[definition.ActionIn] = [(a.type, a.label_id, a.person_id) for a in data.actions]
    persons = dict(Person.objects.filter(workspace=workspace).values_list("id", "archived_at"))
    already_named = definition.named_ids(rule)[1]
    allowed = {
        person_id
        for person_id, archived_at in persons.items()
        if archived_at is None or any_person or person_id in already_named
    }
    definition.validate(
        data.match,
        conditions,
        actions,
        kept_values=definition.phrase_values(rule),
        label_ids=set(Label.objects.filter(workspace=workspace).values_list("id", flat=True)),
        person_ids=allowed,
        archived_person_ids=set(persons) - allowed,
    )
    rule.name = name
    rule.active = data.active
    rule.mode = data.mode
    rule.stop = data.stop
    rule.definition = definition.to_json(data.match, conditions, actions)


def _record(code: AuditCode, workspace: Workspace, actor: AuditActor, rule_id: UUID) -> None:
    """A rule's name can hold a label name, so its row carries the id only."""
    record(
        code,
        actor=actor,
        target=AuditTarget(TargetKind.RULE, rule_id),
        workspace_id=workspace.id,
    )


def _content(rule: Rule) -> tuple[object, ...]:
    """What an update can change, to see whether it changed anything."""
    return (rule.name, rule.active, rule.mode, rule.stop, rule.definition)


def create_rule(*, workspace: Workspace, data: RuleIn, actor: AuditActor) -> Rule:
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
        _record(AuditCode.RULE_CREATED, workspace, actor, rule.id)
        return _rules(workspace).get(id=rule.id)


def update_rule(*, workspace: Workspace, rule_id: UUID, data: RuleIn, actor: AuditActor) -> Rule:
    with transaction.atomic():
        rule = _get_for_update(workspace, rule_id)
        before = _content(rule)
        _fill(rule, workspace, data)
        if _content(rule) != before:
            rule.save()
            _record(AuditCode.RULE_CHANGED, workspace, actor, rule.id)
        return _rules(workspace).get(id=rule.id)


def delete_rule(*, workspace: Workspace, rule_id: UUID, actor: AuditActor) -> None:
    """Its runs go with it; history events keep the rule's name."""
    with transaction.atomic():
        rule = _get_for_update(workspace, rule_id)
        # Its runs and events on letters the caller cannot see go too.
        with system_actor():
            rule.delete()
        _record(AuditCode.RULE_DELETED, workspace, actor, rule_id)


def reorder_rules(*, workspace: Workspace, rule_ids: list[UUID]) -> list[Rule]:
    """Put the rules in this order; every rule of the workspace exactly once."""
    with transaction.atomic():
        # Id order, as everywhere rules are locked (no deadlock with a running rule).
        rules = (
            Rule.objects.select_for_update().filter(workspace=workspace).order_by("id").in_bulk()
        )
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
    _fill(rule, workspace, data, any_person=True)
    spec = definition.to_spec(rule)
    if spec is None:
        raise _invalid(_("Deze regel kunnen we niet lezen."))
    documents = Document.objects.filter(workspace=workspace).exclude(
        status=Document.Status.PROCESSING
    )
    total = documents.count()
    checked = _within_text_budget(documents)
    facts = facts_for_many(checked)
    matched = [d for d in checked if spec.id in evaluate(facts[d.id], [spec]).matched_rule_ids]
    shown = matched[:TEST_SHOWN]
    prefetch_related_objects(
        shown, "addressee_links", "label_links", SUGGESTIONS, HEADLINE_PROPOSALS
    )
    return RuleTestOut(
        total_count=total,
        checked_count=len(checked),
        matched_count=len(matched),
        documents=[document_summary_out(d) for d in shown],
    )


def _within_text_budget(documents: "models.QuerySet[Document]") -> list[Document]:
    """The first of `documents` (at most `TEST_LIMIT`) whose text fits `TEST_TEXT_BUDGET`,
    each with only its first `TEST_TEXT_CHARS` characters of `ocr_text` loaded.

    One query: a running sum over the newest documents stops at the budget. The
    instances are for reading only: saving one would cut its text.
    """
    order = [F("created_at").desc(), F("id").desc()]
    newest = documents.order_by(*order).values("id")[:TEST_LIMIT]
    rows = (
        Document.objects.filter(id__in=newest)
        .annotate(
            text_cost=Least(Length("ocr_text"), Value(TEST_TEXT_CHARS)),
            text_spent=Window(Sum("text_cost"), order_by=order),
            text_head=Left("ocr_text", TEST_TEXT_CHARS),
        )
        .filter(text_spent__lte=TEST_TEXT_BUDGET)
        .defer("ocr_text")
        .order_by(*order)
    )
    result: list[Document] = []
    for document in rows:
        document.ocr_text = document.text_head
        result.append(document)
    return result


def run_rule(*, workspace: Workspace, rule_id: UUID, actor: AuditActor) -> None:
    """Queue applying the rule to the workspace's existing documents."""
    with transaction.atomic():
        rule = Rule.objects.filter(workspace=workspace, id=rule_id).first()
        if rule is None:
            raise rule_not_found()
        if not rule.active:
            raise ApiError(409, "rule_inactive", _("Deze regel staat uit. Zet hem eerst aan."))
        if actor.user_id is None:
            raise ValueError("A rule run is started by a member (R7).")
        # A second click while the first run waits is the same ask.
        jobs.defer_rule_run(workspace.id, rule.id, user_id=actor.user_id)
        _record(AuditCode.RULE_RUN, workspace, actor, rule.id)
