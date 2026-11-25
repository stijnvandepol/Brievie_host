"""Background job: apply one rule to a workspace's existing documents (design 9.4)."""

import logging
from uuid import UUID

from django.db import transaction
from procrastinate.contrib.django import app
from procrastinate.exceptions import AlreadyEnqueued

from apps.documents.models import LIVE, Document
from apps.pipeline.jobs import QUEUE
from apps.rules.definition import to_spec
from apps.rules.engine import RuleSpec, evaluate
from apps.rules.executor import execute
from apps.rules.facts import facts_for, plannable
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership

logger = logging.getLogger(__name__)

BATCH_SIZE = 50

# The queue has one worker. Procrastinate 3 fetches the waiting job with the highest
# priority first (`ORDER BY priority DESC, id ASC` in `procrastinate_fetch_job`), and
# `process_document` has the default 0. Below it, a letter that arrives while a rule
# walks the existing documents is processed before the walk's next batch.
RULE_RUN_PRIORITY = -1


@app.task(name="apply_rule_to_existing", queue=QUEUE, priority=RULE_RUN_PRIORITY)
def apply_rule_to_existing(
    *, workspace_id: str, rule_id: str, after: str = "", user_id: str = ""
) -> None:
    if not user_id:
        # Queued before runs named their starter (P5): fail closed, the admin starts it again.
        logger.info("Rule run skipped: started before the update")
        return
    apply_rule_batch(
        UUID(workspace_id),
        UUID(rule_id),
        user_id=UUID(user_id),
        after=UUID(after) if after else None,
    )


def defer_rule_run(
    workspace_id: UUID, rule_id: UUID, *, user_id: UUID, after: UUID | None = None
) -> bool:
    """Queue a run of the rule from the document after `after` (from the start when None),
    inside the caller's transaction, as the member `user_id` who started it (R7). False
    when a run of this rule is waiting already.

    One waiting run per rule (the queueing lock covers waiting jobs only): a second click
    while the first waits is the same ask, and a running job can queue its next batch.
    """
    arguments = {
        "workspace_id": str(workspace_id),
        "rule_id": str(rule_id),
        "user_id": str(user_id),
    }
    if after is not None:
        arguments["after"] = str(after)
    # The savepoint keeps a refused insert from aborting the caller's transaction.
    try:
        with transaction.atomic():
            apply_rule_to_existing.configure(queueing_lock=f"rule-{rule_id}").defer(**arguments)
    except AlreadyEnqueued:
        return False
    return True


def apply_rule_batch(
    workspace_id: UUID, rule_id: UUID, *, user_id: UUID, after: UUID | None
) -> None:
    """One batch of the documents that are not `processing`, in id order after `after`, in
    one transaction; a full batch queues the next one as a new job, so a waiting letter
    goes first.

    The batch runs as the member who started the run (R7): it reaches only the letters
    that member may see. A member who left meanwhile stops the walk. The rule is read
    again for every batch: once it is deleted or switched off, the walk stops. A document
    someone else is editing right now is waited for, never skipped.
    """
    if not Membership.objects.filter(workspace_id=workspace_id, user_id=user_id).exists():
        logger.info("Rule run skipped: the member left")
        return
    with workspace_context(workspace_id, actor=user_id):
        spec = _active_spec(workspace_id, rule_id)
        if spec is None:
            return
        # Letters only: no rule runs on an own document (archive spec S 6.2).
        documents = Document.objects.filter(
            LIVE, workspace_id=workspace_id, kind=Document.Kind.LETTER
        ).exclude(status=Document.Status.PROCESSING)
        if after is not None:
            documents = documents.filter(id__gt=after)
        ids = list(documents.order_by("id").values_list("id", flat=True)[:BATCH_SIZE])
        if not ids:
            return
        locked = (
            Document.objects.filter(LIVE, id__in=ids, kind=Document.Kind.LETTER)
            .exclude(status=Document.Status.PROCESSING)
            .select_for_update(no_key=True)
            .order_by("id")
        )
        for document in locked:
            execute(
                document,
                evaluate(facts_for(document), [spec]),
                trigger=RuleRun.Trigger.MANUAL,
            )
        if len(ids) == BATCH_SIZE:
            # Refused when the rule was run again meanwhile: that run starts from the
            # beginning and covers the rest.
            defer_rule_run(workspace_id, rule_id, user_id=user_id, after=ids[-1])


def _active_spec(workspace_id: UUID, rule_id: UUID) -> RuleSpec | None:
    rule = Rule.objects.filter(workspace_id=workspace_id, id=rule_id, active=True).first()
    spec = to_spec(rule) if rule is not None else None
    return plannable(workspace_id, [spec])[0] if spec is not None else None
