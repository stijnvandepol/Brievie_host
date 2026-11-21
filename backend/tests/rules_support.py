"""Helpers for the rules tests: rules stored the way the service will store them."""

import uuid
from collections.abc import Sequence

from django.db.models import Max

from apps.documents.models import Document
from apps.labels.models import Label
from apps.rules import definition
from apps.rules.engine import Condition
from apps.rules.models import Rule
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


def make_rule(
    workspace: Workspace,
    *conditions: Condition,
    actions: Sequence[definition.ActionIn],
    name: str = "Regel",
    mode: str = "propose",
    match: str = "all",
    stop: bool = False,
    active: bool = True,
) -> Rule:
    """A rule appended after the workspace's rules."""
    with workspace_context(workspace.id, actor=SYSTEM):
        last = Rule.objects.filter(workspace=workspace).aggregate(last=Max("position"))["last"]
        return Rule.objects.create(
            workspace=workspace,
            position=(last or 0) + 1,
            name=name,
            mode=mode,
            stop=stop,
            active=active,
            definition=definition.to_json(match, conditions, actions),
        )


def add_label(label: Label) -> definition.ActionIn:
    return ("add_label", label.id, None)


def add_addressee(person_id: uuid.UUID) -> definition.ActionIn:
    return ("add_addressee", None, person_id)


def text_word(value: str) -> Condition:
    return Condition("text", "contains_word", value)


def sender_contains(value: str) -> Condition:
    return Condition("sender", "contains", value)


def remove_rules(workspace: Workspace) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        Rule.objects.filter(workspace=workspace).delete()


def make_document(
    workspace: Workspace, text: str = "", *, status: str = "needs_review"
) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text=text, status=status
        )
