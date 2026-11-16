from uuid import UUID

from ninja import Field, Schema

from apps.common.schemas import InputSchema
from apps.documents.schemas import DocumentSummaryOut
from apps.rules.models import Rule


class RuleConditionSchema(InputSchema):
    # text, sender, document_type, label, addressee or source.
    field: str
    # Per field (design 9.2), e.g. contains_word, contains, equals, is, is_not, has, has_not.
    op: str
    # The phrase, document type code, source, or a label or person id; empty for is_empty.
    value: str = ""


class RuleActionSchema(InputSchema):
    # add_label (with label_id) or add_addressee (with person_id).
    type: str
    label_id: UUID | None = None
    person_id: UUID | None = None


class RuleIn(InputSchema):
    name: str = Field(min_length=1, max_length=100)
    active: bool = True
    # propose or auto.
    mode: str = "propose"
    # A matching rule with stop ends the run after its own actions.
    stop: bool = False
    # all (every condition) or any (one of them).
    match: str = "all"
    conditions: list[RuleConditionSchema] = Field(max_length=20)
    actions: list[RuleActionSchema] = Field(max_length=10)


class RuleOut(Schema):
    id: UUID
    position: int
    name: str
    active: bool
    mode: str
    stop: bool
    match: str
    conditions: list[RuleConditionSchema]
    actions: list[RuleActionSchema]
    # The preset label's key for a preset rule; empty for an own rule.
    system_key: str
    # Documents the rule changed, and documents it proposed a change on (one per run).
    applied_count: int
    proposed_count: int

    @staticmethod
    def resolve_match(obj: Rule) -> str:
        match = _definition(obj).get("match")
        return match if isinstance(match, str) else ""

    @staticmethod
    def resolve_conditions(obj: Rule) -> list[RuleConditionSchema]:
        return [
            RuleConditionSchema(
                field=_text(item.get("field")),
                op=_text(item.get("op")),
                value=_text(item.get("value")),
            )
            for item in _items(obj, "conditions")
        ]

    @staticmethod
    def resolve_actions(obj: Rule) -> list[RuleActionSchema]:
        return [
            RuleActionSchema(
                type=_text(item.get("type")),
                label_id=_uuid(item.get("label_id")),
                person_id=_uuid(item.get("person_id")),
            )
            for item in _items(obj, "actions")
        ]


class RuleOrderIn(InputSchema):
    # Every rule of the workspace exactly once, in the new order.
    rule_ids: list[UUID] = Field(max_length=200)


class RuleTestOut(Schema):
    # Documents that are not being processed.
    total_count: int
    # The newest of those, at most 500, that were checked.
    checked_count: int
    # Checked documents whose conditions hold, whether or not the rule would change them.
    matched_count: int
    # The first 50 of the matched documents, newest first.
    documents: list[DocumentSummaryOut]


class RuleRunStartedOut(Schema):
    queued: bool


def _definition(rule: Rule) -> dict[str, object]:
    data: object = rule.definition
    return data if isinstance(data, dict) else {}


def _items(rule: Rule, key: str) -> list[dict[str, object]]:
    items = _definition(rule).get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
