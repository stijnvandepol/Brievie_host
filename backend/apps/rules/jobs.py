"""Background job: apply one rule to a workspace's existing documents (design 9.4)."""

from uuid import UUID

from procrastinate.contrib.django import app

from apps.documents.models import Document
from apps.pipeline.jobs import QUEUE
from apps.rules.definition import to_spec
from apps.rules.engine import RuleSpec, evaluate
from apps.rules.executor import execute
from apps.rules.facts import facts_for
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import workspace_context

BATCH_SIZE = 50


@app.task(name="apply_rule_to_existing", queue=QUEUE)
def apply_rule_to_existing(*, workspace_id: str, rule_id: str) -> None:
    apply_rule(UUID(workspace_id), UUID(rule_id))


def apply_rule(workspace_id: UUID, rule_id: UUID) -> None:
    """Walk the documents that are not `processing` in batches, each its own transaction.

    The rule is read again for every batch: once it is deleted or switched off, the
    walk stops. A document locked by someone else right now is skipped.
    """
    after: UUID | None = None
    while True:
        with workspace_context(workspace_id):
            spec = _active_spec(workspace_id, rule_id)
            if spec is None:
                return
            documents = Document.objects.filter(workspace_id=workspace_id).exclude(
                status=Document.Status.PROCESSING
            )
            if after is not None:
                documents = documents.filter(id__gt=after)
            ids = list(documents.order_by("id").values_list("id", flat=True)[:BATCH_SIZE])
            if not ids:
                return
            locked = (
                Document.objects.filter(id__in=ids)
                .exclude(status=Document.Status.PROCESSING)
                .select_for_update(skip_locked=True)
                .order_by("id")
            )
            for document in locked:
                execute(
                    document,
                    evaluate(facts_for(document), [spec]),
                    trigger=RuleRun.Trigger.MANUAL,
                )
            after = ids[-1]


def _active_spec(workspace_id: UUID, rule_id: UUID) -> RuleSpec | None:
    rule = Rule.objects.filter(workspace_id=workspace_id, id=rule_id, active=True).first()
    return to_spec(rule) if rule is not None else None
