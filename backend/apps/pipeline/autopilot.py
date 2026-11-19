"""The autopilot: apply the AI's strong picks (design (a)) and decide on confirming (b).

Only proposals the pipeline itself made are applied: an AI pick from `AUTO_APPLY_FROM`,
a name match from `PRESELECT_FROM`. A rule's proposal is never applied here, whatever
else proposed the same change: a rule row means the letter's own text can have steered it.

The caller holds the document's row lock and the workspace's RLS context.
"""

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from django.db.models import Case, IntegerField, Value, When

from apps.documents import organise, suggestions
from apps.documents.models import (
    NEEDS_ATTENTION_BELOW,
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.organise import ADDRESSEES_MAX, LABELS_MAX
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.people.matching import PRESELECT_FROM
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.validation import (
    ACTION_TYPES,
    DOCUMENT_TYPES,
    MONTH_NAMES_PATTERN,
    ValidatedValue,
)
from apps.tasks.models import Task

AUTO_APPLY_FROM = Decimal("0.800")

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
_ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE


def apply_open_proposals(document: Document) -> int:
    """Apply the open proposals that qualify; how many were applied.

    Addressees first, then labels, each by confidence (highest first) and id.
    A target that is gone or an archived person is skipped, and so is anything that
    would take the document over its limit.
    """
    room: dict[str, int] = {
        _ADD_ADDRESSEE: ADDRESSEES_MAX
        - DocumentAddressee.objects.filter(document=document).count(),
        _ADD_LABEL: LABELS_MAX - DocumentLabel.objects.filter(document=document).count(),
    }
    rows = (
        DocumentSuggestion.objects.filter(document=document, state=DocumentSuggestion.State.OPEN)
        .select_related("label", "person")
        .annotate(
            order=Case(
                When(action=_ADD_ADDRESSEE, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("order", "-confidence", "id")
    )
    applied = 0
    for row in rows:
        event_origin = _event_origin(row)
        if event_origin is None or room[row.action] <= 0:
            continue
        if row.action == _ADD_LABEL:
            if row.label is None:
                continue
            added = organise.add_label(document, row.label, actor=None, origin=event_origin)
        else:
            if row.person is None or row.person.archived_at is not None:
                continue
            added = organise.add_addressee(document, row.person, actor=None, origin=event_origin)
        if added:
            suggestions.mark_accepted(document, row.action, suggestions.target_id_of(row))
            room[row.action] -= 1
            applied += 1
    return applied


def _event_origin(row: DocumentSuggestion) -> str | None:
    """The history origin when this proposal may be applied on its own, else None."""
    if row.origin == DocumentSuggestion.Origin.AI and row.confidence >= AUTO_APPLY_FROM:
        return DocumentEvent.Origin.AI
    if (
        row.origin == DocumentSuggestion.Origin.NAME_MATCH
        and row.action == _ADD_ADDRESSEE
        and row.confidence >= PRESELECT_FROM
    ):
        return DocumentEvent.Origin.NAME_MATCH
    return None


MAX_ACTIONS = 20
# The limits of `ConfirmIn` and `TaskIn`: a longer value stays a proposal instead of failing.
_MAX_SENDER = 200
_MAX_SUMMARY = 5000
_MAX_DESCRIPTION = 500

_NUMBER_WORDS = (
    "een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|dertien|veertien|vijftien"
    "|zestien|zeventien|achttien|negentien|twintig|dertig"
    "|(?:een|twee|tweeën|drie|vier|vijf|zes|zeven|acht|negen)(?:en|ën)twintig"
)
_TERM_WORDS = re.compile(
    r"uiterlijk|vervaldatum|termijn|\bdeadline|\bten\s+laatste\b", re.IGNORECASE
)
_WITHIN = re.compile(
    r"\bbinnen\s+(?:\d+|" + _NUMBER_WORDS + r")\s+"
    r"(?:dagen|dag|werkdagen|werkdag|weken|week|maanden|maand)\b",
    re.IGNORECASE,
)
_BEFORE_A_DATE = re.compile(
    r"\bv(?:oo|óó|óo|oó)r\s+(?:"
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"
    r"|\d{1,2}\s+(?:" + MONTH_NAMES_PATTERN + r")\b"
    r")",
    re.IGNORECASE,
)


def mentions_term(text: str) -> bool:
    """Whether the letter names a term or deadline in words ("binnen 14 dagen", "uiterlijk",
    "vóór 1 december", "vervaldatum", "termijn", "deadline", "ten laatste").

    Pure. The models miss relative terms, so a letter that names one is not confirmed
    when an action has no date.
    """
    return bool(_WITHIN.search(text) or _BEFORE_A_DATE.search(text) or _TERM_WORDS.search(text))


def decide_confirmation(
    *,
    payload: ExtractionPayload,
    values: Sequence[ValidatedValue],
    text: str,
    today: date,
) -> ConfirmIn | None:
    """The review to confirm on its own, or None when any guard rail fails (design (b)).

    Pure: no database. The open-proposal rail is the caller's. Only values with a source
    in the letter count as a signal; a generated value (type, summary, description) always
    carries 0.500 and is never judged by its confidence.
    """
    sender = _best(values, "sender", 0)
    letter_date = _best(values, "letter_date", 0)
    if sender is None or letter_date is None:
        return None
    if sender.confidence < NEEDS_ATTENTION_BELOW or letter_date.confidence < NEEDS_ATTENTION_BELOW:
        return None
    if len(sender.value) > _MAX_SENDER:
        return None
    day = date.fromisoformat(letter_date.value)
    document_type = DOCUMENT_TYPES.get(payload.document_type.strip().lower())
    if document_type is None or len(payload.actions) > MAX_ACTIONS:
        return None
    tasks: list[TaskIn] = []
    for group, action in enumerate(payload.actions, start=1):
        task = _task(
            action.type, action.description, action.deadline.value, values, group, day, today
        )
        if task is None:
            return None
        tasks.append(task)
    if any(task.deadline is None for task in tasks) and mentions_term(text):
        return None
    summary = _best(values, "summary", 0)
    if summary is not None and len(summary.value) > _MAX_SUMMARY:
        return None
    return ConfirmIn(
        sender=sender.value,
        document_type=document_type,
        letter_date=day,
        summary=summary.value if summary is not None else "",
        summary_by_ai=True,
        tasks=tasks,
        sets_organisation=False,
    )


def _task(
    action_type: str,
    description: str,
    deadline_value: str | None,
    values: Sequence[ValidatedValue],
    group: int,
    letter_date: date,
    today: date,
) -> TaskIn | None:
    code = ACTION_TYPES.get(action_type.strip().lower())
    text = description.strip()
    if code is None or not text or len(text) > _MAX_DESCRIPTION:
        return None
    if not (deadline_value or "").strip():
        return TaskIn(type=code, description=text, deadline_source=Task.DeadlineSource.MANUAL)
    found = _best(values, "action_deadline", group)
    if found is None or found.confidence < NEEDS_ATTENTION_BELOW:
        return None
    deadline = date.fromisoformat(found.value)
    if deadline < today or deadline < letter_date:
        return None
    return TaskIn(
        type=code,
        description=text,
        deadline=deadline,
        deadline_source=Task.DeadlineSource.IN_DOCUMENT,
        deadline_quote=found.quote,
    )


def _best(values: Sequence[ValidatedValue], field: str, group: int) -> ValidatedValue | None:
    """The first validated value of a field in a group."""
    return next((v for v in values if v.field == field and v.group == group), None)
