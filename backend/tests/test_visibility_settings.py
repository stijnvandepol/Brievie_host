"""The service behind changing who sees what (plan Task 37): the snapshot, the token,
the notices' sentences."""

import re

import pytest

from apps.accounts.models import AccountNotice
from apps.accounts.notices import CATEGORY_MESSAGES, NOTICE_VALUE_KEYS
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.people.models import DepartmentMember
from apps.workspaces import visibility_settings
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_settings import Proposal, fingerprint, snapshot
from tests.visibility_support import as_actor, office

PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def test_snapshot_reads_the_office() -> None:
    o = office()
    policy = snapshot(o.workspace)
    assert (
        policy.switches.everyone_sees_all,
        policy.switches.departments_enabled,
        policy.switches.admins_read_all,
    ) == (False, True, False)
    femke = policy.member(o.finance.id)
    assert femke is not None
    assert femke.person_ids == {o.persons["Femke"].id}
    assert femke.department_ids == {o.persons["Finance"].id}
    assert policy.sorter_ids == {o.sorter.id}
    assert set(policy.department_names.values()) == {"Finance", "HR", "Directie"}
    assert "Jan" in policy.person_names.values()
    assert "Finance" not in policy.person_names.values()


def test_fingerprint_changes_with_every_fact() -> None:
    o = office()
    seen = {fingerprint(snapshot(o.workspace))}

    def changed() -> bool:
        token = fingerprint(snapshot(o.workspace))
        new = token not in seen
        seen.add(token)
        return new

    Workspace.objects.filter(id=o.workspace.id).update(admins_read_all=True)
    assert changed()
    Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
    assert changed()
    Membership.objects.filter(workspace=o.workspace, user=o.hr).update(role="admin")
    assert changed()
    with as_actor(o.workspace, SYSTEM):
        DepartmentMember.objects.create(
            workspace=o.workspace, department=o.persons["HR"], user=o.plain
        )
    assert changed()
    # The same state gives the same token.
    assert fingerprint(snapshot(o.workspace)) in seen
    assert not changed()


@pytest.mark.parametrize(
    ("code", "category"),
    [(code, category) for code, messages in CATEGORY_MESSAGES.items() for category in messages],
)
def test_every_category_message_formats_with_its_keys(code: str, category: str) -> None:
    keys = NOTICE_VALUE_KEYS[code]
    message = str(CATEGORY_MESSAGES[code][category])
    assert set(PLACEHOLDER.findall(message)) <= {*keys, "name", "date"}
    sample = {key: f"<{key}>" for key in keys} | {"name": "Anne", "date": "3 mei"}
    assert "%(" not in message % sample
    assert code in AccountNotice.Code.values


def test_change_needs_an_admin_by_the_locked_rows() -> None:
    o = office()
    with pytest.raises(ApiError) as raised:
        visibility_settings.change(
            workspace=o.workspace,
            proposal=Proposal(everyone_sees_all=False),
            actor=AuditActor.for_user(o.plain),
            confirmed=False,
            preview_token="",
        )
    assert raised.value.status == 403
    with pytest.raises(ApiError):
        visibility_settings.change(
            workspace=o.workspace,
            proposal=Proposal(everyone_sees_all=False),
            actor=AuditActor.system(),
            confirmed=False,
            preview_token="",
        )


def test_the_same_values_again_write_nothing() -> None:
    o = office()
    visibility_settings.change(
        workspace=o.workspace,
        proposal=Proposal(everyone_sees_all=False, departments_enabled=True),
        actor=AuditActor.for_user(o.admin),
        confirmed=False,
        preview_token="",
    )
    assert not AccountNotice.objects.filter(user__in=o.users.values()).exists()
