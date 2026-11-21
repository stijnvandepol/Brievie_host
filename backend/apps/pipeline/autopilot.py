"""The autopilot: apply the AI's strong picks (design (a)) and decide on confirming (b).

Applied are an AI pick from `AUTO_APPLY_FROM`, a name match from `PRESELECT_FROM`, and a
rule's label proposal that does not rest on a keyword in the running text
(`rule_labels_to_apply`). A rule's addressee proposal is never applied here: a word in
the letter's own text can have steered it.

The caller holds the document's row lock and the workspace's RLS context.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

from django.db.models import Case, IntegerField, Value, When

from apps.common.privacy import holds_bsn, mask_bsns
from apps.documents import organise, suggestions, visibility
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
from apps.people.models import Person
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.settings_service import autopilot_in_force
from apps.pipeline.validation import (
    ACTION_TYPES,
    DOCUMENT_TYPES,
    MONTH_NAMES_PATTERN,
    ValidatedValue,
)
from apps.rules.engine import evaluate, holds_on_settled
from apps.rules.facts import active_specs, facts_for
from apps.tasks.models import Task
from apps.workspaces.models import Workspace

AUTO_APPLY_FROM = Decimal("0.800")
# More rule labels than this on one letter: the rules cast too wide a net, a person looks.
RULE_LABELS_MAX = 3

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
_ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE


def apply_open_proposals(document: Document) -> int:
    """Apply the open proposals that qualify; how many were applied.

    Addressees first, then labels, each by confidence (highest first) and id.
    A target that is gone or an archived person is skipped, and so is anything that
    would take the document over its limit. An AI pick of a person is applied only when
    its quote names that person (`quote_names`); otherwise it stays open. A rule's label
    proposal is applied only when `rule_labels_to_apply` names it, decided after the
    addressees are applied (they are settled then) and only when the administrator
    agreed to the notice that says so (`Autopilot.rule_labels`). Inside the pipeline's
    addressee scope its own scope is a no-op; called alone, it syncs.
    """
    with visibility.addressee_scope(document):
        return _apply_open_proposals(document)


def _apply_open_proposals(document: Document) -> int:
    room: dict[str, int] = {
        _ADD_ADDRESSEE: ADDRESSEES_MAX
        - DocumentAddressee.objects.filter(document=document).count(),
        _ADD_LABEL: LABELS_MAX - DocumentLabel.objects.filter(document=document).count(),
    }
    rows = list(
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
    active_persons = list(
        Person.objects.filter(workspace_id=document.workspace_id, archived_at__isnull=True)
    )
    rule_labels: frozenset[UUID] | None = None
    # An automatic change never widens a private letter's readers (PB T10): on a letter
    # that is private, or that a Zorg label this run may apply will make private, an
    # addressee stays a proposal. Read from the database, before every addressee: a
    # rule of this run may have made it private.
    zorg_ahead = _zorg_may_be_applied(document, rows)
    applied = 0
    for row in rows:
        if row.action == _ADD_LABEL and rule_labels is None:
            allowed = autopilot_in_force(document.workspace_id).rule_labels
            rule_labels = rule_labels_to_apply(document, rows) if allowed else frozenset()
        event_origin = _event_origin(row, rule_labels or frozenset())
        if event_origin is None or room[row.action] <= 0:
            continue
        if row.action == _ADD_LABEL:
            if row.label is None:
                continue
            added = organise.add_label(
                document,
                row.label,
                actor=None,
                origin=event_origin,
                rule=row.rule if event_origin == DocumentEvent.Origin.RULE_AUTOPILOT else None,
            )
        else:
            if row.person is None or row.person.archived_at is not None:
                continue
            if (
                zorg_ahead
                or visibility.visibility_of(document.id) == Document.Visibility.RESTRICTED
            ):
                continue
            if row.origin == DocumentSuggestion.Origin.AI and (
                not quote_names(row.quote, row.person)
                or named_by_another(row.quote, row.person, active_persons)
            ):
                continue  # the letter's own words must name this person, and only them
            added = organise.add_addressee(document, row.person, actor=None, origin=event_origin)
        if added:
            suggestions.mark_accepted(document, row.action, suggestions.target_id_of(row))
            room[row.action] -= 1
            applied += 1
    return applied


def quote_names(quote: str, person: Person) -> bool:
    """Whether `quote` holds the person's display name or one of their name variants.

    Case-insensitive, on whitespace-normalised text, as whole words. An AI pick of a person
    whose quote does not name them ("Geachte heer/mevrouw") stays a proposal: otherwise the
    letter alone could decide whose post it is. A name holding a (masked) BSN names
    nobody: every letter with any BSN holds the same mask.
    """
    return _named_length(_normalised(quote), person) > 0


def named_by_another(quote: str, person: Person, persons: Sequence[Person]) -> bool:
    """Whether `quote` names another of `persons` with a name at least as long as the
    longest one of `person`'s it names: then it does not tell whose letter it is (as name
    matching, which never preselects such a name)."""
    text = _normalised(quote)
    own = _named_length(text, person)
    return any(other.pk != person.pk and _named_length(text, other) >= own for other in persons)


def _named_length(text: str, person: Person) -> int:
    """The length of the longest of the person's names that `text` holds; 0 for none."""
    longest = 0
    for name in (person.display_name, *person.name_variants):
        if holds_bsn(str(name)):
            continue
        wanted = _normalised(str(name))
        if wanted and re.search(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", text):
            longest = max(longest, len(wanted))
    return longest


def _normalised(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def rule_labels_to_apply(document: Document, rows: Sequence[DocumentSuggestion]) -> frozenset[UUID]:
    """The labels of the open rule proposals in `rows` that the autopilot applies.

    A rule's label qualifies when (a) the rule holds on settled facts alone
    (`holds_on_settled`): the labels and persons on the letter, the sender and type a
    person confirmed, and the letterhead; not on an open proposal, the AI's guess of the
    sender or type, or a word in the running text. Or (b) the AI's own pick of the same
    label reaches `AUTO_APPLY_FROM` (`ai_confidence`). None qualifies when the rules
    disagree on this letter (a rule that matches it adds a label that was said no to here)
    or more than `RULE_LABELS_MAX` qualify: then a person decides. Only a rule that
    matches the document now counts.
    """
    candidates = [
        row
        for row in rows
        if row.action == _ADD_LABEL
        and row.origin == DocumentSuggestion.Origin.RULE
        and row.state == DocumentSuggestion.State.OPEN
        and row.rule_id is not None
        and row.label_id is not None
    ]
    if not candidates:
        return frozenset()
    facts = facts_for(document)
    specs = active_specs(document.workspace_id)
    matched = set(evaluate(facts, specs).matched_rule_ids)
    by_id = {spec.id: spec for spec in specs if spec.id in matched}
    if any(
        action.type == _ADD_LABEL and action.target_id in facts.dismissed_label_ids
        for spec in by_id.values()
        for action in spec.actions
    ):
        return frozenset()
    confirmed = document.confirmed_at is not None
    settled = replace(
        facts,
        sender=document.sender if confirmed else "",
        document_type=document.document_type if confirmed else "",
        hoped_label_ids=frozenset(),
        hoped_addressee_ids=frozenset(),
    )
    qualifying = frozenset(
        row.label_id
        for row in candidates
        if row.label_id is not None
        and row.rule_id is not None
        and (spec := by_id.get(row.rule_id)) is not None
        and (row.ai_confidence >= AUTO_APPLY_FROM or holds_on_settled(spec, settled))
    )
    return qualifying if len(qualifying) <= RULE_LABELS_MAX else frozenset()


def _private(document: Document) -> bool:
    return visibility.visibility_of(document.id) == Document.Visibility.RESTRICTED


def _zorg_may_be_applied(document: Document, rows: Sequence[DocumentSuggestion]) -> bool:
    """Whether an open Zorg label proposal could be applied in this run where Zorg makes a
    letter private. A rule's proposal counts whatever `rule_labels_to_apply` will say:
    that is decided after the addressees, so this blocks rather than guesses."""
    if not Workspace.objects.filter(id=document.workspace_id, care_is_private=True).exists():
        return False
    return any(
        row.action == _ADD_LABEL
        and row.label is not None
        and row.label.system_key == organise.ZORG
        and (
            row.origin == DocumentSuggestion.Origin.RULE
            or _event_origin(row, frozenset()) is not None
        )
        for row in rows
    )


def _event_origin(row: DocumentSuggestion, rule_labels: frozenset[UUID]) -> str | None:
    """The history origin when this proposal may be applied on its own, else None."""
    if row.origin == DocumentSuggestion.Origin.RULE:
        if row.action == _ADD_LABEL and row.label_id in rule_labels:
            return DocumentEvent.Origin.RULE_AUTOPILOT
        return None
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

_TENS = "twintig|dertig|veertig|vijftig|zestig|zeventig|tachtig|negentig"
_NUMBER_WORDS = (
    "een|één|eén|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|elf|twaalf|dertien|veertien"
    "|vijftien|zestien|zeventien|achttien|negentien|honderd|" + _TENS + "|"
    "(?:een|één|eén|twee|tweeën|drie|vier|vijf|zes|zeven|acht|negen)(?:en|ën)(?:" + _TENS + ")"
)
_ONE_COUNT = r"(?:\d+|" + _NUMBER_WORDS + r")"
# "veertien (14)", "2 (twee)": legal Dutch repeats the number in brackets.
_BRACKETED = r"\s*\(\s*" + _ONE_COUNT + r"\s*\)"
_COUNT = _ONE_COUNT + r"(?:" + _BRACKETED + r")?"
_UNITS = (
    r"(?:dagen|dag|werkdagen|werkdag|kalenderdagen|kalenderdag"
    r"|weken|week|maanden|maand|jaren|jaar)"
)
_TERM_WORDS = re.compile(
    r"uiterlijk|uiterste|vervaldag|vervaldatum|betaaldatum|termijn|\bdeadline"
    r"|\bten\s+laatste\b|\bniet\s+later\s+dan\b|\btot\s+en\s+met\b|\bt/m\b"
    r"|\bdue\s+date\b|\bno\s+later\s+than\b",
    re.IGNORECASE,
)
# "binnen 14 dagen", "binnen veertien (14) dagen", "binnen 14dagen" (OCR drops the space).
_WITHIN = re.compile(r"\bbinnen\s+" + _COUNT + r"\s*" + _UNITS + r"\b", re.IGNORECASE)
# "30 dagen netto", "veertien dagen na factuurdatum", "zes weken na de dagtekening",
# and any bracketed count of a unit ("zes (6) weken").
_PAYMENT_TERM = re.compile(
    r"\b" + _COUNT + r"\s*" + _UNITS + r"\s+(?:netto\b|na\b)"
    r"|\b" + _ONE_COUNT + _BRACKETED + r"\s*" + _UNITS + r"\b",
    re.IGNORECASE,
)
_WEEKDAYS = "maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag"
_DATE = (
    r"(?:\d{4}-\d{2}-\d{2}"
    # "1-12", "01-12-26", "1/12/2026"; not "1.500" (an amount)
    r"|\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?(?!\d)"
    r"|\d{1,2}\s+(?:" + MONTH_NAMES_PATTERN + r")\b)"
)
# "vóór 1 december", "voor: maandag 01-12-2026", "tot 1 december"
_BEFORE_A_DATE = re.compile(
    r"\b(?:v[oó][oó]r|tot)[:\s]+(?:(?:" + _WEEKDAYS + r")\s+)?" + _DATE,
    re.IGNORECASE,
)
_EN_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_EN_COUNT = (
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    r"|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|sixty|ninety)"
)
# "within 14 days", "before 1 December 2026", "before December 1"
_ENGLISH = re.compile(
    r"\bwithin\s+" + _EN_COUNT + r"\s*(?:(?:business|working|calendar)\s+)?"
    r"(?:days?|weeks?|months?|years?)\b"
    r"|\bbefore\s+(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:" + _EN_MONTHS + r")\b"
    r"|(?:" + _EN_MONTHS + r")\s+\d{1,2}\b"
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?(?!\d))",
    re.IGNORECASE,
)


def mentions_term(text: str) -> bool:
    """Whether the letter names a term or deadline in words ("binnen 14 dagen",
    "binnen veertien (14) dagen", "zes weken na de dagtekening", "uiterlijk", "vóór 1
    december", "tot 1 december", "vervaldag", "betaaldatum", "30 dagen netto", "niet later
    dan", "tot en met", "termijn", "deadline", "ten laatste", and a few English forms:
    "within 14 days", "due date", "no later than", "before 1 December").

    Pure, and linear in the text (no nested quantifiers). The models miss relative terms,
    so a letter that names one is not confirmed unless every action has a date. The text
    is read in NFC (OCR and PDF text often holds "vóór" as `o` plus a combining accent).
    Blocking too often is better than too rarely: a word that merely contains "termijn"
    matches too.
    """
    text = unicodedata.normalize("NFC", text)
    return bool(
        _WITHIN.search(text)
        or _PAYMENT_TERM.search(text)
        or _BEFORE_A_DATE.search(text)
        or _TERM_WORDS.search(text)
        or _ENGLISH.search(text)
    )


_TERM_PATTERNS = (_WITHIN, _PAYMENT_TERM, _BEFORE_A_DATE, _TERM_WORDS, _ENGLISH)
# The end of the sentence a term is in: a stop followed by a space or the end, or a line
# break. A stop inside a date ("1.12.2026") or an amount does not end it.
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)|\n")
_WHITESPACE = re.compile(r"\s+")
TERM_PHRASE_MAX = 200


def term_phrase(text: str) -> str:
    """The words of the letter that name a term, for a task without a date ("binnen zes
    weken na dagtekening bezwaar maken"): from the first place `mentions_term` matches to
    the end of that sentence, whitespace collapsed, BSNs masked, at most 200 characters
    (cut on a word boundary). Empty exactly when `mentions_term(text)` is false.

    Pure and linear in the text: the same compiled patterns as `mentions_term`, each
    searched once, then one search for the sentence's end.
    """
    text = unicodedata.normalize("NFC", text)
    found = [m for m in (p.search(text) for p in _TERM_PATTERNS) if m is not None]
    if not found:
        return ""
    first = min(found, key=lambda m: m.start())
    start = first.start()
    end_match = _SENTENCE_END.search(text, first.end())
    end = end_match.start() if end_match is not None else len(text)
    # Room for masking and collapsing before the cut; never more than needed.
    raw = text[start : min(end, start + 4 * TERM_PHRASE_MAX)]
    phrase = _WHITESPACE.sub(" ", mask_bsns(raw)).strip()
    if len(phrase) > TERM_PHRASE_MAX:
        cut = phrase[: TERM_PHRASE_MAX + 1]
        space = cut.rfind(" ")
        phrase = cut[:space] if space > 0 else phrase[:TERM_PHRASE_MAX]
    return phrase.rstrip(" ,:-")


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
    if day > today:
        return None  # a letter is not dated in the future: a misread
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
    # The letter names a term: every action must carry a date, and a letter without
    # actions waits for a person (the model may have missed the action).
    if mentions_term(text) and (not tasks or any(task.deadline is None for task in tasks)):
        return None
    # The invoice names a due date (any confidence): there must be a payment, and every
    # payment must carry a deadline.
    if any(v.field == "invoice_due_date" for v in values):
        payments = [task for task in tasks if task.type == Task.Type.PAY]
        if not payments or any(task.deadline is None for task in payments):
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
    amount = _quoted_amount(values) if code == Task.Type.PAY else ""
    if not (deadline_value or "").strip():
        return TaskIn(
            type=code,
            description=text,
            deadline_source=Task.DeadlineSource.MANUAL,
            amount=amount,
        )
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
        amount=amount,
    )


_PLAIN_AMOUNT = re.compile(r"[0-9]{1,10}\.[0-9]{2}")


def _quoted_amount(values: Sequence[ValidatedValue]) -> str:
    """The invoice's amount for a payment, only when its quote holds it (the validation
    gives zero confidence otherwise): "1234.56", else empty."""
    found = _best(values, "invoice_amount_incl", 0)
    if found is None or found.confidence <= 0 or not _PLAIN_AMOUNT.fullmatch(found.value):
        return ""
    return found.value


def _best(values: Sequence[ValidatedValue], field: str, group: int) -> ValidatedValue | None:
    """The first validated value of a field in a group."""
    return next((v for v in values if v.field == field and v.group == group), None)
