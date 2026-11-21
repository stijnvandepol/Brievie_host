"""The people writers act with the role of the locked membership, not the one an endpoint
read before the lock (security review of PR #39, finding 3)."""

from collections.abc import Callable

import pytest
from django.test import Client

from apps.audit.codes import ActorKind
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.people import services as people
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.locks import lock_memberships
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.visibility_support import Office, as_actor, office


def _demote_at_the_lock(monkeypatch: pytest.MonkeyPatch, o: Office) -> None:
    """Anne loses her admin role after the endpoint read it, before the writer's lock."""
    original: Callable[[Workspace], list[Membership]] = lock_memberships

    def demote_then_lock(workspace: Workspace) -> list[Membership]:
        Membership.objects.filter(workspace=workspace, user=o.hr).update(role="admin")
        Membership.objects.filter(workspace=workspace, user=o.admin).update(role="member")
        return original(workspace)

    monkeypatch.setattr(people, "lock_memberships", demote_then_lock)


def test_a_link_by_an_admin_demoted_before_the_lock_is_403(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = office()
    o.state(e=True, d=True, a=True)  # nobody gains: only the role decides
    newcomer = UserFactory.create()
    Membership.objects.create(workspace=o.workspace, user=newcomer, role="member")
    _demote_at_the_lock(monkeypatch, o)
    client.force_login(o.admin)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Jan'].id}/member",
        {"user_id": str(newcomer.id)},
        content_type="application/json",
    )
    assert r.status_code == 403, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert Person.objects.get(id=o.persons["Jan"].id).user_id is None


@pytest.mark.parametrize("operation", ["add", "remove", "lead"])
def test_a_department_change_by_an_admin_demoted_before_the_lock_is_403(
    client: Client, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    o = office()
    o.state(e=True, d=True, a=True)
    finance = o.persons["Finance"]
    _demote_at_the_lock(monkeypatch, o)
    client.force_login(o.admin)
    base = f"/api/v1/workspaces/{o.workspace.id}/persons/{finance.id}/members"
    if operation == "add":
        r = client.post(base, {"user_id": str(o.plain.id)}, content_type="application/json")
    elif operation == "remove":
        r = client.delete(f"{base}/{o.finance.id}")
    else:
        r = client.put(f"{base}/{o.finance.id}", {"is_lead": True}, content_type="application/json")
    assert r.status_code == 403, r.json()
    with as_actor(o.workspace, SYSTEM):
        rows = set(
            DepartmentMember.objects.filter(department=finance).values_list("user_id", "is_lead")
        )
    assert rows == {(o.finance.id, False)}


def test_a_call_without_a_user_is_the_system_only_when_it_says_so() -> None:
    o = office()
    o.state(e=True, d=True, a=True)
    newcomer = UserFactory.create()
    Membership.objects.create(workspace=o.workspace, user=newcomer, role="member")
    nobody = AuditActor(kind=ActorKind.USER, user_id=None, name="")
    with as_actor(o.workspace, SYSTEM):
        with pytest.raises(ApiError) as raised:
            people.link_member(
                workspace=o.workspace,
                person_id=o.persons["Jan"].id,
                user_id=newcomer.id,
                actor=nobody,
                actor_role=people.ACTING_AS_SYSTEM,
            )
        assert raised.value.status == 403
        with pytest.raises(ApiError):
            people.add_department_member(
                workspace=o.workspace,
                person_id=o.persons["Finance"].id,
                user_id=newcomer.id,
                actor=nobody,
            )
        person = people.link_member(
            workspace=o.workspace,
            person_id=o.persons["Jan"].id,
            user_id=newcomer.id,
            actor=AuditActor.system(),
            actor_role=people.ACTING_AS_SYSTEM,
        )
    assert person.user_id == newcomer.id
