from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.rules import services
from apps.rules.models import Rule
from apps.rules.schemas import RuleIn, RuleOrderIn, RuleOut, RuleRunStartedOut, RuleTestOut
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["rules"])

_RULES = "/workspaces/{workspace_id}/rules"
_RULE = "/workspaces/{workspace_id}/rules/{rule_id}"


@router.get(
    _RULES,
    response={200: list[RuleOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listRules",
    description="The workspace's rules in the order they run.",
)
def list_rules(request: HttpRequest, workspace_id: UUID) -> list[Rule]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return services.list_rules(membership.workspace)


@router.post(
    _RULES,
    response={201: RuleOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createRule",
    description="Appended after the other rules; at most 200 per workspace.",
)
def create_rule(request: HttpRequest, workspace_id: UUID, payload: RuleIn) -> Status[Rule]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return Status(201, services.create_rule(workspace=membership.workspace, data=payload))


# Before `_RULE`, so `order` and `test` are not read as a rule id.
@router.put(
    f"{_RULES}/order",
    response={200: list[RuleOut], 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="reorderRules",
    description="Every rule of the workspace exactly once, in the new order; else 422.",
)
def reorder_rules(request: HttpRequest, workspace_id: UUID, payload: RuleOrderIn) -> list[Rule]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.reorder_rules(workspace=membership.workspace, rule_ids=payload.rule_ids)


@router.post(
    f"{_RULES}/test",
    response={200: RuleTestOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="testRule",
    description=(
        "Checks the unsaved rule against the newest 500 documents that are not being "
        "processed and counts those whose conditions hold. Stores nothing."
    ),
)
def test_rule(request: HttpRequest, workspace_id: UUID, payload: RuleIn) -> RuleTestOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.test_rule(workspace=membership.workspace, data=payload)


@router.put(
    _RULE,
    response={200: RuleOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="updateRule",
)
def update_rule(request: HttpRequest, workspace_id: UUID, rule_id: UUID, payload: RuleIn) -> Rule:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return services.update_rule(workspace=membership.workspace, rule_id=rule_id, data=payload)


@router.delete(
    _RULE,
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="deleteRule",
    description="History events keep the rule's name.",
)
def delete_rule(request: HttpRequest, workspace_id: UUID, rule_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    services.delete_rule(workspace=membership.workspace, rule_id=rule_id)
    return Status(204, None)


@router.post(
    f"{_RULE}/run",
    response={202: RuleRunStartedOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="runRule",
    description=(
        "Queues applying the rule to the existing documents in the background. "
        "409 `rule_inactive` when the rule is switched off."
    ),
)
def run_rule(request: HttpRequest, workspace_id: UUID, rule_id: UUID) -> Status[RuleRunStartedOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    services.run_rule(workspace=membership.workspace, rule_id=rule_id)
    return Status(202, RuleRunStartedOut(queued=True))
