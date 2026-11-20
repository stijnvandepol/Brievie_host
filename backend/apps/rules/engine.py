"""The rules engine: document facts and rules in, planned actions out (design 9.3).

A pure function without Django or a database, so "test this rule" and the real run use
the same code. The engine keeps two states while it walks the rules: settled (the facts
plus every planned `auto` action) and hoped (settled plus the open suggestions plus
every planned `propose` action). A rule matches on the hoped state; its actions are
`auto` only when the rule is automatic and also matches the settled state. A change
the user dismissed on the document is never planned, so no later rule builds on it.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from apps.common.text import Prepared, excerpt, find_phrase, prepare, searchable

# The operators each condition field allows (design 9.2).
OPERATORS: Mapping[str, frozenset[str]] = {
    "text": frozenset({"contains_word", "contains", "not_contains_word"}),
    "sender": frozenset({"contains_word", "contains", "equals"}),
    "document_type": frozenset({"is", "is_not"}),
    "label": frozenset({"has", "has_not"}),
    "addressee": frozenset({"is", "is_empty"}),
    "source": frozenset({"is"}),
}

ACTIONS = frozenset({"add_label", "add_addressee"})


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str


@dataclass(frozen=True)
class Action:
    type: str
    target_id: UUID


@dataclass(frozen=True)
class RuleSpec:
    """A parsed, validated, active rule; rules reach `evaluate` in position order."""

    id: UUID
    name: str
    match: str
    conditions: tuple[Condition, ...]
    actions: tuple[Action, ...]
    mode: str
    stop: bool


@dataclass(frozen=True)
class DocumentFacts:
    text: str
    sender: str
    document_type: str
    source: str
    label_ids: frozenset[UUID]
    addressee_ids: frozenset[UUID]

    # Open suggestions on the document: not there yet, but hoped for.
    hoped_label_ids: frozenset[UUID] = frozenset()
    hoped_addressee_ids: frozenset[UUID] = frozenset()

    # Changes the user dismissed on the document: never planned, so never built on.
    dismissed_label_ids: frozenset[UUID] = frozenset()
    dismissed_addressee_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class PlannedAction:
    rule_id: UUID
    rule_name: str
    action: str
    target_id: UUID
    mode: str
    quote: str


@dataclass(frozen=True)
class Evaluation:
    matched_rule_ids: tuple[UUID, ...]
    planned: tuple[PlannedAction, ...]


@dataclass
class _State:
    label_ids: set[UUID] = field(default_factory=set)
    addressee_ids: set[UUID] = field(default_factory=set)

    def targets(self, action: str) -> set[UUID]:
        return self.label_ids if action == "add_label" else self.addressee_ids


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


@dataclass
class _Phrases:
    """The text and sender, normalised once per evaluation, and the outcome of every
    text or sender condition asked so far: they do not depend on the settled or hoped
    state, so each is worked out once (a letter can be 600,000 characters)."""

    text: Prepared
    sender: Prepared
    results: dict[Condition, tuple[bool, str]] = field(default_factory=dict)

    def holds(self, condition: Condition) -> tuple[bool, str]:
        result = self.results.get(condition)
        if result is None:
            haystack = self.text if condition.field == "text" else self.sender
            result = _phrase(haystack, condition.op, condition.value)
            self.results[condition] = result
        return result


def _phrase(haystack: Prepared, op: str, value: str) -> tuple[bool, str]:
    """Whether a text or sender operator holds, and the quote when it found words."""
    original = haystack.original
    # A value that holds the BSN mask token (or nothing) says nothing about a letter, so
    # the condition never holds, also negated: "does not contain [BSN]" would otherwise
    # hold for every letter (common.text).
    needle = searchable(value)
    if not needle:
        return False, ""
    if op == "equals":
        if haystack.normalised != needle:
            return False, ""
        return True, excerpt(original, (0, len(original)))
    if op not in ("contains_word", "contains", "not_contains_word"):
        return False, ""
    span = find_phrase(haystack, value, whole_words=op != "contains")
    if op == "not_contains_word":
        return span is None, ""
    if span is None:
        return False, ""
    return True, excerpt(original, span)


def _id_condition(ids: set[UUID], op: str, value: str) -> bool:
    if op == "is_empty":
        return not ids
    target = _uuid(value)
    if target is None:
        return False
    if op in ("has", "is"):
        return target in ids
    if op == "has_not":
        return target not in ids
    return False


def _condition(
    condition: Condition, facts: DocumentFacts, phrases: _Phrases, state: _State
) -> tuple[bool, str]:
    """Whether `condition` holds on `state`, and its quote (empty when it has none)."""
    field_, op, value = condition.field, condition.op, condition.value
    if op not in OPERATORS.get(field_, frozenset()):
        return False, ""
    if field_ in ("text", "sender"):
        return phrases.holds(condition)
    if field_ == "label":
        return _id_condition(state.label_ids, op, value), ""
    if field_ == "addressee":
        return _id_condition(state.addressee_ids, op, value), ""
    actual = facts.document_type if field_ == "document_type" else facts.source
    return (actual == value) == (op != "is_not"), ""


_COMBINE: Mapping[str, Callable[[Iterable[bool]], bool]] = {"all": all, "any": any}


def _matches(
    rule: RuleSpec, facts: DocumentFacts, phrases: _Phrases, state: _State
) -> tuple[bool, str]:
    """Whether `rule` matches `state`, and the quote of its first matching phrase."""
    combine = _COMBINE.get(rule.match)
    if combine is None:
        return False, ""
    results = [_condition(c, facts, phrases, state) for c in rule.conditions]
    holds = combine(held for held, _ in results)
    quote = next((q for held, q in results if held and q), "") if holds else ""
    return holds, quote


def evaluate(facts: DocumentFacts, rules: Sequence[RuleSpec]) -> Evaluation:
    """Plan the actions of `rules` on a document (design 9.3)."""
    phrases = _Phrases(prepare(facts.text), prepare(facts.sender))
    settled = _State(set(facts.label_ids), set(facts.addressee_ids))
    hoped = _State(
        settled.label_ids | facts.hoped_label_ids,
        settled.addressee_ids | facts.hoped_addressee_ids,
    )
    # Targets this run already planned; open suggestions are not in it, so a rule still
    # plans a target that is only proposed by name matching or the AI (the suggestion
    # then gets the rule as its origin).
    proposed = _State()
    dismissed = _State(set(facts.dismissed_label_ids), set(facts.dismissed_addressee_ids))
    matched: list[UUID] = []
    planned: list[PlannedAction] = []
    for rule in rules:
        holds, quote = _matches(rule, facts, phrases, hoped)
        if not holds:
            continue
        matched.append(rule.id)
        auto = rule.mode == "auto" and _matches(rule, facts, phrases, settled)[0]
        for action in rule.actions:
            if action.type not in ACTIONS:
                continue
            target = action.target_id
            if target in settled.targets(action.type) or target in dismissed.targets(action.type):
                continue
            # Already planned in this run: only an automatic action adds something (it
            # settles it).
            if target in proposed.targets(action.type) and not auto:
                continue
            planned.append(
                PlannedAction(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    action=action.type,
                    target_id=target,
                    mode="auto" if auto else "propose",
                    quote=quote,
                )
            )
            hoped.targets(action.type).add(target)
            proposed.targets(action.type).add(target)
            if auto:
                settled.targets(action.type).add(target)
        if rule.stop:
            break
    return Evaluation(matched_rule_ids=tuple(matched), planned=tuple(planned))


# The letterhead: the start of the text up to the first blank line or the salutation,
# at most this many non-empty lines and characters. Scanned letters seldom keep their
# blank lines, so the salutation and the caps usually decide: sender, address, date,
# reference and subject line ("Betreft", "Aan") fit in eight.
LETTERHEAD_LINES = 8
LETTERHEAD_CHARS = 400
# A line that opens the letter itself; it and what follows are running text.
_SALUTATION = re.compile(r"(?:geachte|beste|hallo|dear|l\.\s?s\.)(?!\w)", re.IGNORECASE)

# Conditions that hold because something is there (not because something is absent).
_POSITIVE: Mapping[str, frozenset[str]] = {
    "text": frozenset({"contains_word", "contains"}),
    "sender": frozenset({"contains_word", "contains", "equals"}),
    "document_type": frozenset({"is"}),
    "label": frozenset({"has"}),
    "addressee": frozenset({"is"}),
    "source": frozenset({"is"}),
}


def letterhead(text: str) -> str:
    """The letterhead of a letter's text (see `LETTERHEAD_LINES`).

    Leading blank lines are skipped. A word cut by the character cap is left out, so a
    part of a longer word ("Belastingdienst" of "Belastingdienstverlening") is no hit.
    """
    start = text.lstrip()
    head = start[:LETTERHEAD_CHARS]
    if len(start) > LETTERHEAD_CHARS and not start[LETTERHEAD_CHARS].isspace():
        cut = max((i for i, char in enumerate(head) if char.isspace()), default=-1)
        head = head[: cut + 1] if cut >= 0 else ""
    lines: list[str] = []
    for line in head.splitlines():
        if not line.strip() or _SALUTATION.match(line.strip()):
            break
        lines.append(line)
        if len(lines) == LETTERHEAD_LINES:
            break
    return "\n".join(lines).rstrip()


def holds_on_settled(rule: RuleSpec, facts: DocumentFacts) -> bool:
    """Whether the rule matches on settled facts alone and not on a keyword in the
    running text, with at least one condition that holds because something is there.

    `facts` holds only what is settled: the caller passes the sender and the type a
    person confirmed (else empty) and no hoped ids. A word condition holds in the
    letterhead; a sender condition on the (confirmed) sender or with its words in the
    letterhead; a negated word is looked for in the whole text. A rule that holds only
    by absences ("does not contain") does not count.
    """
    combine = _COMBINE.get(rule.match)
    if combine is None:
        return False
    head_text = letterhead(facts.text)
    head = prepare(head_text)
    sender = prepare(facts.sender)
    whole: Prepared | None = None
    held: list[bool] = []
    present = False
    for condition in rule.conditions:
        field_, op, value = condition.field, condition.op, condition.value
        if op not in OPERATORS.get(field_, frozenset()):
            holds = False
        elif field_ == "text" and op == "not_contains_word":
            whole = whole or prepare(facts.text)
            holds = _phrase(whole, op, value)[0]
        elif field_ == "text":
            holds = _phrase(head, op, value)[0]
        elif field_ == "sender" and op == "equals":
            holds = _phrase(sender, op, value)[0] or any(
                _phrase(prepare(line), op, value)[0] for line in head_text.splitlines()
            )
        elif field_ == "sender":
            holds = _phrase(sender, op, value)[0] or _phrase(head, op, value)[0]
        elif field_ == "label":
            holds = _id_condition(set(facts.label_ids), op, value)
        elif field_ == "addressee":
            holds = _id_condition(set(facts.addressee_ids), op, value)
        else:
            actual = facts.document_type if field_ == "document_type" else facts.source
            holds = bool(actual) and (actual == value) == (op != "is_not")
        held.append(holds)
        present = present or (holds and op in _POSITIVE.get(field_, frozenset()))
    return combine(held) and present
