"""The rules engine: document facts and rules in, planned actions out (design 9.3).

A pure function without Django or a database, so "test this rule" and the real run use
the same code. The engine keeps two states while it walks the rules: settled (the facts
plus every planned `auto` action) and hoped (settled plus the open suggestions plus
every planned `propose` action). A rule matches on the hoped state; its actions are
`auto` only when the rule is automatic and also matches the settled state. A change
the user dismissed on the document is never planned, so no later rule builds on it.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from apps.common.text import Prepared, excerpt, find_phrase, normalise, prepare

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
    if op == "equals":
        if haystack.normalised != normalise(value):
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
