"""Rules, labels, persons and the AI setting write their audit rows (D 11).

A row is written in the transaction of the change it describes, once per change; a
label or rule row holds an id only, never a name.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.labels import services as label_services
from apps.labels.models import Label
from apps.people import services as people_services
from apps.pipeline.models import AISettings
from apps.rules import services as rule_services
from apps.rules.models import Rule
from apps.rules.schemas import RuleIn
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import make_person
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.members_support import admin_of, as_user, join, workspace_rows

ACTOR = AuditActor.system()


def rule_in(ws: Workspace) -> RuleIn:
    """A valid rule that adds one of the workspace's preset labels."""
    with workspace_context(ws.id, actor=SYSTEM):
        # A preset label: the tests that delete an own label must never pick that one.
        label = Label.objects.filter(workspace=ws).exclude(system_key="").order_by("id").first()
    assert label is not None
    return RuleIn.model_validate(
        {
            "name": "Geheim label Belasting",
            "conditions": [{"field": "text", "op": "contains_word", "value": "APK"}],
            "actions": [{"type": "add_label", "label_id": str(label.id)}],
        }
    )


def _label(ws: Workspace) -> Label:
    with workspace_context(ws.id, actor=SYSTEM):
        return Label.objects.create(workspace=ws, name="Privé-label")


def _rule(ws: Workspace) -> Rule:
    with workspace_context(ws.id, actor=SYSTEM):
        return rule_services.create_rule(workspace=ws, data=rule_in(ws), actor=ACTOR)


def _rows(ws: Workspace, code: str) -> list[WorkspaceAuditEvent]:
    return workspace_rows(ws, code)


def test_create_update_delete_rule_write_their_rows(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        rule = rule_services.create_rule(workspace=ws_a, data=rule_in(ws_a), actor=ACTOR)
        rule_services.update_rule(
            workspace=ws_a,
            rule_id=rule.id,
            data=rule_in(ws_a).model_copy(update={"name": "Nieuwe naam"}),
            actor=ACTOR,
        )
        rule_services.delete_rule(workspace=ws_a, rule_id=rule.id, actor=ACTOR)

    for code in (AuditCode.RULE_CREATED, AuditCode.RULE_CHANGED, AuditCode.RULE_DELETED):
        (row,) = _rows(ws_a, code)
        assert (row.target_kind, row.target_id, row.target_name) == (TargetKind.RULE, rule.id, "")
        assert row.actor_kind == "system"


def test_run_rule_writes_its_row(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        rule = rule_services.create_rule(workspace=ws_a, data=rule_in(ws_a), actor=ACTOR)
        rule_services.run_rule(
            workspace=ws_a, rule_id=rule.id, actor=AuditActor.for_user(admin_of(ws_a))
        )

    (row,) = _rows(ws_a, AuditCode.RULE_RUN)
    assert row.target_id == rule.id


def test_label_deleted_writes_an_id_only_row(ws_a: Workspace) -> None:
    label = _label(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        label_services.delete_label(workspace=ws_a, label_id=label.id, actor=ACTOR, membership=None)

    (row,) = _rows(ws_a, AuditCode.LABEL_DELETED)
    assert (row.target_kind, row.target_id, row.target_name) == (TargetKind.LABEL, label.id, "")


def test_person_deleted_and_linked_rows_carry_the_name(ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma Jansen")
    member = join(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=member.id,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=None,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )
        people_services.delete_person(workspace=ws_a, person_id=person.id, actor=ACTOR)

    for code in (AuditCode.PERSON_LINKED, AuditCode.PERSON_UNLINKED, AuditCode.PERSON_DELETED):
        (row,) = _rows(ws_a, code)
        assert (row.target_kind, row.target_id, row.target_name) == (
            TargetKind.PERSON,
            person.id,
            "Oma Jansen",
        )


def test_a_refused_change_writes_no_row(ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma")
    stranger = UserFactory.create()
    with workspace_context(ws_a.id, actor=SYSTEM), pytest.raises(ApiError):
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=stranger.id,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )

    assert _rows(ws_a, AuditCode.PERSON_LINKED) == []


def test_one_link_writes_exactly_one_row(ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma")
    member = join(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=member.id,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )

    assert len(_rows(ws_a, AuditCode.PERSON_LINKED)) == 1


def test_label_and_rule_rows_carry_no_name(ws_a: Workspace) -> None:
    label = _label(ws_a)
    rule = _rule(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        label_services.delete_label(workspace=ws_a, label_id=label.id, actor=ACTOR, membership=None)
        rule_services.delete_rule(workspace=ws_a, rule_id=rule.id, actor=ACTOR)

    with workspace_context(ws_a.id, actor=SYSTEM):
        for row in WorkspaceAuditEvent.objects.filter(
            code__in=[AuditCode.LABEL_DELETED, AuditCode.RULE_CREATED, AuditCode.RULE_DELETED]
        ):
            assert row.target_name == ""
            assert row.target_id is not None
            assert "Privé-label" not in repr(row.values)
            assert "Geheim" not in repr(row.values)


def test_rule_endpoints_write_the_row_for_the_admin(client: Client, ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client.force_login(admin)
    created = post_json(
        client,
        f"/workspaces/{ws_a.id}/rules",
        rule_in(ws_a).model_dump(mode="json"),
    )
    assert created.status_code == 201, created.content

    (row,) = _rows(ws_a, AuditCode.RULE_CREATED)
    assert (row.actor_kind, row.actor_id, row.actor_name) == ("user", admin.id, admin.name)
    assert str(row.target_id) == created.json()["id"]


def test_label_delete_endpoint_writes_the_row(client: Client, ws_a: Workspace) -> None:
    label = _label(ws_a)
    member = join(ws_a)

    r = as_user(member).delete(f"/api/v1/workspaces/{ws_a.id}/labels/{label.id}")

    assert r.status_code == 204
    (row,) = _rows(ws_a, AuditCode.LABEL_DELETED)
    assert (row.actor_id, row.target_id) == (member.id, label.id)


def test_ai_settings_change_is_logged_beside_the_consent_event(
    client: Client, ws_a: Workspace
) -> None:
    admin = admin_of(ws_a)
    client.force_login(admin)

    r = client.put(
        f"/api/v1/workspaces/{ws_a.id}/ai",
        {
            "mode": "external",
            "api_key": "nep-sleutel",
            "consent_address": "https://api.mistral.ai/v1",
        },
        content_type="application/json",
    )

    assert r.status_code == 200, r.content
    (row,) = _rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED)
    assert row.actor_name == admin.name
    assert row.actor_id == admin.id
    assert row.values == {"setting": "ai", "value": "external"}
    assert "nep-sleutel" not in repr(row.values)


def test_an_unchanged_ai_off_writes_no_row(client: Client, ws_a: Workspace) -> None:
    client.force_login(admin_of(ws_a))

    r = client.put(
        f"/api/v1/workspaces/{ws_a.id}/ai", {"mode": "off"}, content_type="application/json"
    )

    assert r.status_code == 200
    assert _rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED) == []


def test_configure_ai_command_is_logged_as_the_system(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", "nep-sleutel")

    call_command(
        "configure_ai",
        "--workspace",
        str(ws_a.id),
        "--mode",
        "external",
        "--accept-external-ai",
        stdout=StringIO(),
    )

    (row,) = _rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED)
    assert (row.actor_kind, row.actor_id, row.actor_name) == ("system", None, "Brievie")
    assert row.values == {"setting": "ai", "value": "external"}
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert AISettings.objects.get().mode == "external"


def test_a_failed_ai_change_writes_no_audit_row(client: Client, ws_a: Workspace) -> None:
    client.force_login(admin_of(ws_a))

    r = client.put(
        f"/api/v1/workspaces/{ws_a.id}/ai",
        {"mode": "external", "api_key": "k"},
        content_type="application/json",
    )

    assert r.status_code == 409
    assert _rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED) == []


def test_updating_a_rule_to_what_it_already_is_writes_no_row(ws_a: Workspace) -> None:
    rule = _rule(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        rule_services.update_rule(workspace=ws_a, rule_id=rule.id, data=rule_in(ws_a), actor=ACTOR)
        rule_services.update_rule(workspace=ws_a, rule_id=rule.id, data=rule_in(ws_a), actor=ACTOR)

    assert _rows(ws_a, AuditCode.RULE_CHANGED) == []

    changed = rule_in(ws_a).model_copy(update={"name": "Anders", "stop": True})
    with workspace_context(ws_a.id, actor=SYSTEM):
        rule_services.update_rule(workspace=ws_a, rule_id=rule.id, data=changed, actor=ACTOR)
        rule_services.update_rule(workspace=ws_a, rule_id=rule.id, data=changed, actor=ACTOR)
    assert len(_rows(ws_a, AuditCode.RULE_CHANGED)) == 1


def test_linking_a_person_to_the_user_already_linked_writes_no_row(ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma")
    member = join(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=member.id,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )
        people_services.link_member(
            workspace=ws_a,
            person_id=person.id,
            user_id=member.id,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )
        other = make_person(ws_a, "Opa")
        # Unlinking somebody who was never linked is no change either.
        people_services.link_member(
            workspace=ws_a,
            person_id=other.id,
            user_id=None,
            actor=ACTOR,
            actor_role=people_services.ACTING_AS_SYSTEM,
        )

    assert len(_rows(ws_a, AuditCode.PERSON_LINKED)) == 1
    assert _rows(ws_a, AuditCode.PERSON_UNLINKED) == []
