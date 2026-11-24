import uuid

import pytest
from django.test import RequestFactory
from pytest_django import Settings

from apps.audit.codes import ALLOWED_VALUES, NAMED_TARGETS, AuditCode, TargetKind
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.services import AuditActor, AuditTarget, actor_from_request, record
from apps.devices.models import Device
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.factories import UserFactory


def workspace_rows(workspace: Workspace) -> list[WorkspaceAuditEvent]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(WorkspaceAuditEvent.objects.all())


def test_record_writes_a_workspace_row_in_its_own_context(ws_a: Workspace, ws_b: Workspace) -> None:
    user = UserFactory.create(name="Anne")
    target_id = uuid.uuid4()

    record(
        AuditCode.MEMBER_ROLE_CHANGED,
        actor=AuditActor.for_user(user, client_ip="192.0.2.7"),
        target=AuditTarget(kind="user", id=target_id, name="Bram"),
        workspace_id=ws_a.id,
        values={"from": "viewer", "to": "member"},
    )

    rows = workspace_rows(ws_a)
    assert len(rows) == 1
    row = rows[0]
    assert row.workspace_id == ws_a.id
    assert row.code == "member_role_changed"
    assert (row.actor_kind, row.actor_id, row.actor_name) == ("user", user.id, "Anne")
    assert row.actor_device_id is None
    assert (row.target_kind, row.target_id, row.target_name) == ("user", target_id, "Bram")
    assert row.client_ip == "192.0.2.7"
    assert row.values == {"from": "viewer", "to": "member"}
    assert workspace_rows(ws_b) == []
    # Forced row-level security: without a context nothing is visible.
    assert list(WorkspaceAuditEvent.objects.all()) == []
    assert ServerAuditEvent.objects.count() == 0


def test_record_nested_in_another_workspace_context_writes_to_its_own(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id, actor=SYSTEM):
        record(AuditCode.RULE_DELETED, actor=AuditActor.system(), workspace_id=ws_a.id)
        assert WorkspaceAuditEvent.objects.count() == 0
    assert len(workspace_rows(ws_a)) == 1


def test_record_without_workspace_writes_a_server_row() -> None:
    record(
        AuditCode.SIGN_IN_SUCCEEDED,
        actor=AuditActor.system(),
        values={"via": "password"},
    )

    row = ServerAuditEvent.objects.get()
    assert row.code == "sign_in_succeeded"
    assert (row.actor_kind, row.actor_id, row.actor_name) == ("system", None, "Brievie")
    assert (row.target_kind, row.target_id, row.target_name) == ("", None, "")
    assert row.values == {"via": "password"}


def test_record_rejects_a_value_key_not_listed_for_the_code(ws_a: Workspace) -> None:
    with pytest.raises(ValueError, match="name"):
        record(
            AuditCode.LABEL_DELETED,
            actor=AuditActor.system(),
            target=AuditTarget(kind="label", id=uuid.uuid4()),
            workspace_id=ws_a.id,
            values={"name": "Zorg"},
        )
    assert workspace_rows(ws_a) == []


@pytest.mark.parametrize("value", [1.5, None, ["a"], {"a": 1}])
def test_record_rejects_a_value_that_is_not_text_number_or_flag(
    ws_a: Workspace, value: object
) -> None:
    with pytest.raises(ValueError, match="role"):
        record(
            AuditCode.INVITATION_CREATED,
            actor=AuditActor.system(),
            workspace_id=ws_a.id,
            values={"role": value},  # type: ignore[dict-item]
        )
    assert workspace_rows(ws_a) == []


def test_record_rejects_a_code_outside_the_closed_list() -> None:
    with pytest.raises(ValueError, match="document_read"):
        record("document_read", actor=AuditActor.system())  # type: ignore[arg-type]
    assert ServerAuditEvent.objects.count() == 0


def test_record_rejects_an_unknown_actor_or_target_kind() -> None:
    with pytest.raises(ValueError, match="actor"):
        record(
            AuditCode.SESSION_ENDED,
            actor=AuditActor(kind="robot", user_id=None, name="x"),
        )
    with pytest.raises(ValueError, match="target"):
        record(
            AuditCode.SESSION_ENDED,
            actor=AuditActor.system(),
            target=AuditTarget(kind="letter", id=uuid.uuid4()),
        )
    assert ServerAuditEvent.objects.count() == 0


def test_record_cuts_long_strings(ws_a: Workspace) -> None:
    record(
        AuditCode.WORKSPACE_SETTING_CHANGED,
        actor=AuditActor(kind="user", user_id=None, name="A" * 400),
        target=AuditTarget(kind="workspace", id=ws_a.id, name="B" * 400),
        workspace_id=ws_a.id,
        values={"setting": "s" * 300, "value": True},
    )

    row = workspace_rows(ws_a)[0]
    assert row.values == {"setting": "s" * 200, "value": True}
    assert row.actor_name == "A" * 150
    assert row.target_name == "B" * 150


def test_record_masks_a_bsn_in_a_name(ws_a: Workspace) -> None:
    record(
        AuditCode.PERSON_DELETED,
        actor=AuditActor(kind="user", user_id=None, name="Anne 111222333"),
        target=AuditTarget(kind="person", id=uuid.uuid4(), name="Jan 111222333"),
        workspace_id=ws_a.id,
    )

    row = workspace_rows(ws_a)[0]
    assert row.target_name == "Jan [BSN]"
    assert row.actor_name == "Anne [BSN]"


def test_record_masks_a_bsn_in_a_value(ws_a: Workspace) -> None:
    record(
        AuditCode.EMERGENCY_ACCESS_USED,
        actor=AuditActor.system(),
        workspace_id=ws_a.id,
        values={"reason": "Brief over 111222333", "document_count": 2},
    )

    assert workspace_rows(ws_a)[0].values == {"reason": "Brief over [BSN]", "document_count": 2}


def test_actor_from_request_names_the_device(rf: RequestFactory, settings: Settings) -> None:
    settings.BRIEVIE_PROXY_COUNT = 0
    user = UserFactory.create(name="Anne")
    device = Device.objects.create(user=user, name="iPhone", token_hash="0" * 64)
    request = rf.get("/", REMOTE_ADDR="192.0.2.9")
    request.user = user
    request.device = device  # type: ignore[attr-defined]

    actor = actor_from_request(request)

    assert actor == AuditActor(
        kind="device", user_id=user.id, name="Anne", device_id=device.id, client_ip="192.0.2.9"
    )


def test_actor_from_request_without_a_device_is_the_user(
    rf: RequestFactory, settings: Settings
) -> None:
    settings.BRIEVIE_PROXY_COUNT = 1
    user = UserFactory.create(name="Anne")
    request = rf.get("/", REMOTE_ADDR="172.18.0.3", HTTP_X_FORWARDED_FOR="unknown")
    request.user = user

    actor = actor_from_request(request)

    # A forged address is not stored: the column is `inet`.
    assert actor == AuditActor(kind="user", user_id=user.id, name="Anne")


def test_every_code_has_an_allowed_values_entry() -> None:
    assert set(ALLOWED_VALUES) == set(AuditCode)
    assert all(isinstance(keys, frozenset) for keys in ALLOWED_VALUES.values())


def test_the_list_of_codes_is_the_closed_list_of_d11() -> None:
    assert {code.value for code in AuditCode} == {
        "sign_in_succeeded",
        "sign_in_failed",
        "sign_in_locked_out",
        "second_factor_added",
        "second_factor_removed",
        "second_factor_cleared",
        "second_factor_recovery_used",
        "second_factor_recovery_renewed",
        "password_changed",
        "password_reset_created",
        "session_ended",
        "device_paired",
        "device_revoked",
        "device_expired",
        "server_admin_granted",
        "server_admin_revoked",
        "recovery_sheet_viewed",
        "backup_requested",
        "sso_settings_changed",
        "workspace_created",
        "invitation_created",
        "invitation_revoked",
        "invitation_accepted",
        "member_role_changed",
        "member_removed",
        "member_left",
        "workspace_setting_changed",
        "workspace_preset_applied",
        "post_sorter_changed",
        "rule_created",
        "rule_changed",
        "rule_deleted",
        "rule_run",
        "label_deleted",
        "person_deleted",
        "person_created",
        "person_manager_set",
        "person_manager_cleared",
        "visibility_changed",
        "person_linked",
        "person_unlinked",
        "department_member_added",
        "department_member_removed",
        "department_lead_changed",
        "emergency_access_switched_on",
        "emergency_access_switched_off",
        "emergency_access_used",
        "export_created",
        "export_downloaded",
        "document_trashed",
        "document_restored",
        "document_purged",
    }


NAMED_TARGET_KINDS = ["user", "person", "invitation", "device", "workspace"]
ID_ONLY_TARGET_KINDS = ["label", "rule", "document", "export", "session"]


def test_every_target_kind_is_decided() -> None:
    assert set(NAMED_TARGET_KINDS) | set(ID_ONLY_TARGET_KINDS) == set(TargetKind.values)
    assert frozenset(NAMED_TARGET_KINDS) == NAMED_TARGETS


@pytest.mark.parametrize("kind", NAMED_TARGET_KINDS)
def test_record_keeps_the_name_of_a_named_target(ws_a: Workspace, kind: str) -> None:
    record(
        AuditCode.PERSON_DELETED,
        actor=AuditActor.system(),
        target=AuditTarget(kind=kind, id=uuid.uuid4(), name="Bram"),
        workspace_id=ws_a.id,
    )
    assert workspace_rows(ws_a)[0].target_name == "Bram"


@pytest.mark.parametrize("kind", ID_ONLY_TARGET_KINDS)
def test_record_refuses_a_name_for_a_target_that_carries_an_id_only(
    ws_a: Workspace, kind: str
) -> None:
    with pytest.raises(ValueError, match=kind):
        record(
            AuditCode.LABEL_DELETED,
            actor=AuditActor.system(),
            target=AuditTarget(kind=kind, id=uuid.uuid4(), name="Zorgverzekering"),
            workspace_id=ws_a.id,
        )
    assert workspace_rows(ws_a) == []


@pytest.mark.parametrize("kind", ID_ONLY_TARGET_KINDS)
def test_record_takes_an_id_only_target_without_a_name(ws_a: Workspace, kind: str) -> None:
    target_id = uuid.uuid4()
    record(
        AuditCode.LABEL_DELETED,
        actor=AuditActor.system(),
        target=AuditTarget(kind=kind, id=target_id),
        workspace_id=ws_a.id,
    )
    row = workspace_rows(ws_a)[0]
    assert (row.target_kind, row.target_id, row.target_name) == (kind, target_id, "")


@pytest.mark.parametrize("address", ["unknown", "1.2.3.4:80", "fe80::1%eth0", "", "x" * 300])
def test_record_stores_an_unusable_client_address_as_null(address: str) -> None:
    record(
        AuditCode.SIGN_IN_FAILED,
        actor=AuditActor(kind="user", user_id=None, name="", client_ip=address),
    )
    assert ServerAuditEvent.objects.get().client_ip is None


def test_record_keeps_a_usable_client_address() -> None:
    record(
        AuditCode.SIGN_IN_FAILED,
        actor=AuditActor(kind="user", user_id=None, name="", client_ip="2001:db8::1"),
    )
    assert ServerAuditEvent.objects.get().client_ip == "2001:db8::1"


def test_a_lockout_row_says_where_it_came_from() -> None:
    # via: password (sign-in) or password_change (wrong current passwords).
    assert ALLOWED_VALUES[AuditCode.SIGN_IN_LOCKED_OUT] == frozenset({"via"})
    record(
        AuditCode.SIGN_IN_LOCKED_OUT,
        actor=AuditActor.system(),
        values={"via": "password_change"},
    )
    with pytest.raises(ValueError, match="does not take"):
        record(AuditCode.SIGN_IN_LOCKED_OUT, actor=AuditActor.system(), values={"email": "x"})


@pytest.mark.parametrize(
    ("code", "keys"),
    [
        ("second_factor_added", {"replaced"}),
        ("second_factor_cleared", {"via", "devices_revoked"}),
        ("second_factor_recovery_used", {"left"}),
        ("second_factor_recovery_renewed", set()),
        ("sign_in_failed", {"step"}),
        ("sign_in_succeeded", {"via", "second_factor"}),
        ("sign_in_locked_out", {"via"}),
        ("session_ended", {"count", "reason"}),
    ],
)
def test_two_step_sign_in_codes_take_their_keys(code: str, keys: set[str]) -> None:
    assert ALLOWED_VALUES[AuditCode(code)] == keys


def test_a_second_factor_row_refuses_another_key() -> None:
    with pytest.raises(ValueError, match="code"):
        record(
            AuditCode.SECOND_FACTOR_RECOVERY_USED,
            actor=AuditActor.system(),
            values={"code": "ABCDE-FGHJK"},
        )
