"""A rule's definition: validating it, writing it as JSON and reading it back (design 9.2).

The engine trusts what it gets, so the limits live here: an empty condition list or an
empty value would match every document.
"""

import logging
from collections.abc import Collection, Mapping, Sequence
from uuid import UUID

from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.common.privacy import holds_bsn, mask_bsns
from apps.documents.models import Document
from apps.rules.engine import ACTIONS, OPERATORS, Action, Condition, RuleSpec
from apps.rules.models import Rule

logger = logging.getLogger(__name__)

VERSION = 1
MATCHES = ("all", "any")
CONDITIONS_MIN, CONDITIONS_MAX = 1, 20
ACTIONS_MIN, ACTIONS_MAX = 1, 10
# Length of a condition value after trimming, per field.
VALUE_LENGTH: Mapping[str, tuple[int, int]] = {"text": (2, 100), "sender": (2, 200)}

# (type, label_id, person_id) as the API carries an action.
ActionIn = tuple[str, UUID | None, UUID | None]


def validate(
    match: str,
    conditions: Sequence[Condition],
    actions: Sequence[ActionIn],
    *,
    label_ids: Collection[UUID],
    person_ids: Collection[UUID],
    kept_values: Collection[str] = (),
    archived_person_ids: Collection[UUID] = (),
) -> None:
    """Refuse a definition that breaks a limit, with a Dutch sentence that names it.

    `label_ids` are the workspace's labels, `person_ids` the persons the rule may name
    (see rules.services), `archived_person_ids` the archived ones it may not, only to
    say so. `kept_values` are the text and sender values the rule already stores: one
    masked before a BSN was refused here may stay (it matches nothing).
    """
    if match not in MATCHES:
        raise _invalid(_("Kies of alle voorwaarden moeten kloppen of een van de voorwaarden."))
    if not CONDITIONS_MIN <= len(conditions) <= CONDITIONS_MAX:
        raise _invalid(
            _("Een regel heeft %(min)d tot %(max)d voorwaarden.")
            % {"min": CONDITIONS_MIN, "max": CONDITIONS_MAX}
        )
    if not ACTIONS_MIN <= len(actions) <= ACTIONS_MAX:
        raise _invalid(
            _("Een regel heeft %(min)d tot %(max)d acties.")
            % {"min": ACTIONS_MIN, "max": ACTIONS_MAX}
        )
    for condition in conditions:
        _validate_condition(
            condition,
            label_ids=label_ids,
            person_ids=person_ids,
            kept_values=kept_values,
            archived_person_ids=archived_person_ids,
        )
    for action in actions:
        _validate_action(
            action,
            label_ids=label_ids,
            person_ids=person_ids,
            archived_person_ids=archived_person_ids,
        )


def _validate_condition(
    condition: Condition,
    *,
    label_ids: Collection[UUID],
    person_ids: Collection[UUID],
    kept_values: Collection[str],
    archived_person_ids: Collection[UUID],
) -> None:
    field, op = condition.field, condition.op
    if field not in OPERATORS:
        raise _invalid(_("Een voorwaarde gaat over iets wat we niet kennen."))
    if op not in OPERATORS[field]:
        raise _invalid(_("Een voorwaarde heeft een vergelijking die bij dit veld niet past."))
    value = condition.value.strip()
    if field in VALUE_LENGTH:
        low, high = VALUE_LENGTH[field]
        if not low <= len(value) <= high:
            raise _invalid(
                _("De tekst van een voorwaarde is %(min)d tot %(max)d tekens.")
                % {"min": low, "max": high}
            )
        # Letters hold every BSN as the same mask, so a BSN would find any letter with one.
        if holds_bsn(value) and value not in kept_values:
            raise _invalid(
                _(
                    "Een BSN bewaren we nooit, dus je kunt het niet in een regel gebruiken. "
                    "Laat het nummer weg."
                )
            )
    elif field == "document_type":
        if value not in Document.Type.values:
            raise _invalid(_("Dit soort document kennen we niet."))
    elif field == "source":
        if value not in Document.Source.values:
            raise _invalid(_("Deze bron kennen we niet."))
    elif field == "label":
        if _uuid(value) not in label_ids:
            raise _invalid(_("Een voorwaarde noemt een label dat we niet kunnen vinden."))
    elif op == "is_empty":
        if value:
            raise _invalid(_("Bij 'op naam van niemand' hoort geen persoon."))
    elif _uuid(value) not in person_ids:
        if _uuid(value) in archived_person_ids:
            raise _archived_person()
        raise _invalid(_("Een voorwaarde noemt een persoon die we niet kunnen vinden."))


def _validate_action(
    action: ActionIn,
    *,
    label_ids: Collection[UUID],
    person_ids: Collection[UUID],
    archived_person_ids: Collection[UUID],
) -> None:
    kind, label_id, person_id = action
    if kind not in ACTIONS:
        raise _invalid(_("Deze actie kennen we niet."))
    if kind == "add_label":
        if person_id is not None or label_id is None:
            raise _invalid(_("Een actie 'label toevoegen' noemt alleen een label."))
        if label_id not in label_ids:
            raise _invalid(_("Een actie noemt een label dat we niet kunnen vinden."))
        return
    if label_id is not None or person_id is None:
        raise _invalid(_("Een actie 'op naam zetten' noemt alleen een persoon."))
    if person_id not in person_ids:
        if person_id in archived_person_ids:
            raise _archived_person()
        raise _invalid(_("Een actie noemt een persoon die we niet kunnen vinden."))


def _archived_person() -> ApiError:
    return _invalid(
        _("Deze persoon is gearchiveerd. Zet de persoon eerst terug, of kies een ander.")
    )


def to_json(
    match: str, conditions: Sequence[Condition], actions: Sequence[ActionIn]
) -> dict[str, object]:
    """The stored form of a validated definition; values trimmed and BSN-masked."""
    return {
        "version": VERSION,
        "match": match,
        "conditions": [
            {"field": c.field, "op": c.op, "value": _stored_value(c)} for c in conditions
        ],
        "actions": [
            {"type": kind, "label_id": _str(label_id), "person_id": _str(person_id)}
            for kind, label_id, person_id in actions
        ],
    }


def _stored_value(condition: Condition) -> str:
    """A phrase trimmed and BSN-masked; a label or person id in its one form."""
    value = condition.value.strip()
    if condition.field in VALUE_LENGTH:
        return mask_bsns(value)
    if condition.field in ("label", "addressee") and (target := _uuid(value)) is not None:
        return str(target)
    return value


def to_spec(rule: Rule) -> RuleSpec | None:
    """The rule as the engine takes it; None (and a warning) when its definition is broken.

    A broken rule is skipped, the other rules still run.
    """
    spec = _parse(rule)
    if spec is None:
        logger.warning("Skipped rule %s: its definition cannot be read", rule.id)
    return spec


def _parse(rule: Rule) -> RuleSpec | None:
    data: object = rule.definition
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return None
    match = data.get("match")
    raw_conditions = data.get("conditions")
    raw_actions = data.get("actions")
    if match not in MATCHES or not isinstance(raw_conditions, list):
        return None
    if not isinstance(raw_actions, list) or not isinstance(match, str):
        return None
    conditions = [_parse_condition(item) for item in raw_conditions]
    actions = [_parse_action(item) for item in raw_actions]
    valid_conditions = [c for c in conditions if c is not None]
    valid_actions = [a for a in actions if a is not None]
    if len(valid_conditions) != len(conditions) or len(valid_actions) != len(actions):
        return None
    if not CONDITIONS_MIN <= len(valid_conditions) <= CONDITIONS_MAX:
        return None
    if not ACTIONS_MIN <= len(valid_actions) <= ACTIONS_MAX:
        return None
    return RuleSpec(
        id=rule.id,
        name=rule.name,
        match=match,
        conditions=tuple(valid_conditions),
        actions=tuple(valid_actions),
        mode=rule.mode,
        stop=rule.stop,
    )


def _parse_condition(item: object) -> Condition | None:
    if not isinstance(item, dict):
        return None
    field, op, value = item.get("field"), item.get("op"), item.get("value")
    if not isinstance(field, str) or not isinstance(op, str) or not isinstance(value, str):
        return None
    if op not in OPERATORS.get(field, frozenset()):
        return None
    if field in VALUE_LENGTH and not value.strip():
        # An empty phrase would be found in every letter.
        return None
    return Condition(field=field, op=op, value=value)


def _parse_action(item: object) -> Action | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    key = "label_id" if kind == "add_label" else "person_id"
    if kind not in ACTIONS or not isinstance(kind, str):
        return None
    target = item.get(key)
    target_id = _uuid(target) if isinstance(target, str) else None
    if target_id is None:
        return None
    return Action(type=kind, target_id=target_id)


def named_ids(rule: Rule) -> tuple[frozenset[UUID], frozenset[UUID]]:
    """The label ids and the person ids the rule names, in conditions or actions.

    Read from the raw definition, so a rule that cannot be parsed still counts.
    """
    labels: set[UUID] = set()
    persons: set[UUID] = set()
    data: object = rule.definition
    if not isinstance(data, dict):
        return frozenset(), frozenset()
    for item in _list(data.get("conditions")):
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            continue
        target = _uuid(str(item["value"]))
        if target is not None and item.get("field") == "label":
            labels.add(target)
        elif target is not None and item.get("field") == "addressee":
            persons.add(target)
    for item in _list(data.get("actions")):
        if not isinstance(item, dict):
            continue
        for key, found in (("label_id", labels), ("person_id", persons)):
            value = item.get(key)
            target = _uuid(value) if isinstance(value, str) else None
            if target is not None:
                found.add(target)
    return frozenset(labels), frozenset(persons)


def phrase_values(rule: Rule) -> frozenset[str]:
    """The text and sender values the rule stores (none for a rule not saved yet)."""
    data: object = rule.definition
    if not isinstance(data, dict):
        return frozenset()
    return frozenset(
        str(item["value"])
        for item in _list(data.get("conditions"))
        if isinstance(item, dict)
        and item.get("field") in VALUE_LENGTH
        and isinstance(item.get("value"), str)
    )


def only_adds_label(rule: Rule, label_id: UUID) -> bool:
    """Whether the rule's only action is adding this label."""
    data: object = rule.definition
    if not isinstance(data, dict):
        return False
    actions = _list(data.get("actions"))
    if len(actions) != 1 or not isinstance(actions[0], dict):
        return False
    action = actions[0]
    return action.get("type") == "add_label" and action.get("label_id") == str(label_id)


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _str(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)
