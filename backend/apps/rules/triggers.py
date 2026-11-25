"""When rules run on their own (design 9.4): after processing and after confirming.

Never on a hand edit of labels or addressees. The caller holds the workspace's RLS
context and the document's row lock.
"""

from apps.documents.models import Document
from apps.rules.engine import evaluate
from apps.rules.executor import execute
from apps.rules.facts import active_specs, facts_for
from apps.rules.models import RuleRun


def on_processed(document: Document) -> None:
    """At the end of the pipeline, after name matching and the AI's picks are stored."""
    _run(document, RuleRun.Trigger.PROCESSED)


def on_confirmed(document: Document) -> None:
    """At the end of a confirm, with the confirmed sender and type."""
    _run(document, RuleRun.Trigger.CONFIRMED)


def _run(document: Document, trigger: str) -> None:
    # No rule runs on an own document (archive spec S 6.2): it gets no proposals or tasks.
    if document.kind != Document.Kind.LETTER:
        return
    specs = active_specs(document.workspace_id)
    if not specs:
        return
    execute(document, evaluate(facts_for(document), specs), trigger=trigger)
