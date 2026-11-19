from uuid import UUID

import pytest
from django.test import override_settings

from apps.audit.codes import AuditCode
from apps.audit.models import WorkspaceAuditEvent
from apps.pipeline import settings_service
from apps.pipeline.models import AISettings
from apps.pipeline.settings_service import Actor, AutopilotError
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of

MISTRAL = "https://api.mistral.ai/v1"
OPENAI = "https://api.openai.com/v1"


def external(workspace: Workspace, *, supplier: str = "mistral", model: str = "") -> None:
    settings_service.configure(
        workspace_id=workspace.id,
        mode="external",
        supplier=supplier,
        base_url="",
        model=model,
        api_key="nep-sleutel-voor-tests",
        consent=True,
    )


def switch_off(workspace: Workspace) -> None:
    settings_service.configure(
        workspace_id=workspace.id, mode="off", base_url="", model="", api_key=None
    )


def set_autopilot(
    workspace: Workspace,
    *,
    organise: bool,
    confirm: bool,
    acknowledged: bool = True,
    user_id: UUID | None = None,
) -> settings_service.Autopilot:
    return settings_service.configure_autopilot(
        workspace_id=workspace.id,
        organise=organise,
        confirm=confirm,
        acknowledged=acknowledged,
        actor=Actor(user_id=user_id, label="api"),
    )


def rows(workspace: Workspace) -> list[WorkspaceAuditEvent]:
    return list(
        WorkspaceAuditEvent.objects.filter(
            workspace_id=workspace.id,
            code=AuditCode.WORKSPACE_SETTING_CHANGED,
            values__setting__in=["ai_auto_organise", "ai_auto_confirm"],
        ).order_by("created_at", "id")
    )


def values(workspace: Workspace) -> list[tuple[object, object]]:
    return [(r.values["setting"], r.values["value"]) for r in rows(workspace)]


def test_without_a_settings_row_everything_is_off(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        assert settings_service.autopilot(ws_a.id) == settings_service.Autopilot(
            organise=False, confirm=False, allowed=True
        )


def test_switching_on_without_ai_needs_ai(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        with pytest.raises(AutopilotError) as no_row:
            set_autopilot(ws_a, organise=True, confirm=False)
        AISettings.objects.create(workspace=ws_a, mode="off")
        with pytest.raises(AutopilotError) as mode_off:
            set_autopilot(ws_a, organise=False, confirm=True)
        assert rows(ws_a) == []
    assert no_row.value.reason == mode_off.value.reason == "needs_ai"


def test_switching_on_when_the_server_forbids_it_is_forbidden(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        with (
            override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False),
            pytest.raises(AutopilotError) as error,
        ):
            set_autopilot(ws_a, organise=True, confirm=False)
        assert error.value.reason == "forbidden"
        assert settings_service.autopilot(ws_a.id).organise is False


def test_switching_on_without_acknowledgement_is_refused(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        with pytest.raises(AutopilotError) as error:
            set_autopilot(ws_a, organise=True, confirm=True, acknowledged=False)
        assert error.value.reason == "ack_required"
        assert settings_service.autopilot(ws_a.id).organise is False
        assert rows(ws_a) == []


def test_switching_on_with_acknowledgement_audits_the_user(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        external(ws_a)
        result = set_autopilot(ws_a, organise=True, confirm=False, user_id=admin.id)
        assert result == settings_service.Autopilot(organise=True, confirm=False, allowed=True)
        (row,) = rows(ws_a)
    assert row.values == {"setting": "ai_auto_organise", "value": True}
    assert (row.actor_id, row.actor_name) == (admin.id, admin.name)


def test_switching_off_needs_no_acknowledgement_and_is_audited(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        set_autopilot(ws_a, organise=False, confirm=False, acknowledged=False)
        assert settings_service.autopilot(ws_a.id).organise is False
        assert values(ws_a)[2:] == [("ai_auto_organise", False), ("ai_auto_confirm", False)]


def test_switching_off_works_even_when_the_server_forbids_it(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=False)
        with override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False):
            result = set_autopilot(ws_a, organise=False, confirm=False, acknowledged=False)
        assert result.organise is False


def test_no_change_writes_no_audit_row(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=False)
        set_autopilot(ws_a, organise=True, confirm=False)
        assert values(ws_a) == [("ai_auto_organise", True)]


def test_staying_on_needs_no_new_acknowledgement(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=False)
        # confirm goes on, organise stays on: only the new switch needs the ack.
        with pytest.raises(AutopilotError) as error:
            set_autopilot(ws_a, organise=True, confirm=True, acknowledged=False)
        assert error.value.reason == "ack_required"
        set_autopilot(ws_a, organise=True, confirm=False, acknowledged=False)


def test_switching_ai_off_resets_both_switches_and_audits(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        settings_service.configure(
            workspace_id=ws_a.id,
            mode="off",
            base_url="",
            model="",
            api_key=None,
            actor=Actor(user_id=admin.id, label="api"),
        )
        stored = AISettings.objects.get(workspace=ws_a)
        assert (stored.auto_organise, stored.auto_confirm) == (False, False)
        assert values(ws_a)[2:] == [("ai_auto_organise", False), ("ai_auto_confirm", False)]
        assert {r.actor_id for r in rows(ws_a)[2:]} == {admin.id}


def test_off_and_on_again_leaves_both_switches_off(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        switch_off(ws_a)
        external(ws_a)
        assert settings_service.autopilot(ws_a.id) == settings_service.Autopilot(
            organise=False, confirm=False, allowed=True
        )


def test_consent_to_another_address_resets_both_switches(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        external(ws_a, supplier="openai", model="gpt-test")
        stored = AISettings.objects.get(workspace=ws_a)
        assert stored.consented_base_url == OPENAI
        assert (stored.auto_organise, stored.auto_confirm) == (False, False)
        assert values(ws_a)[2:] == [("ai_auto_organise", False), ("ai_auto_confirm", False)]


def test_another_model_at_the_same_address_keeps_the_switches(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        external(ws_a, model="mistral-large-latest")
        assert settings_service.autopilot(ws_a.id).organise is True
        assert len(rows(ws_a)) == 2


def test_in_force_is_off_when_the_server_forbids_it_even_if_stored_on(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        assert settings_service.autopilot_in_force(ws_a.id) == settings_service.Autopilot(
            organise=True, confirm=True, allowed=True
        )
        with override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False):
            assert settings_service.autopilot_in_force(ws_a.id) == settings_service.Autopilot(
                organise=False, confirm=False, allowed=False
            )
            # What is stored stays visible.
            assert settings_service.autopilot(ws_a.id).organise is True


def test_in_force_is_off_when_the_mode_is_off_even_if_stored_on(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        set_autopilot(ws_a, organise=True, confirm=True)
        # A row changed behind the service's back.
        AISettings.objects.filter(workspace=ws_a).update(mode="off")
        assert settings_service.autopilot_in_force(ws_a.id) == settings_service.Autopilot(
            organise=False, confirm=False, allowed=True
        )


def test_the_notice_says_what_the_user_must_know() -> None:
    notice = settings_service.autopilot_notice()
    for part in (
        "Automatisch door AI",
        "fouten",
        "deadline",
        "bevestigt",
        "niemand",
        "terugdraaien",
    ):
        assert part.lower() in notice.lower()
