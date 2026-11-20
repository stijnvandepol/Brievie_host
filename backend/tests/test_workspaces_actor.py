"""The actor the visibility policies read: the helpers (PB T1, plan Task 3) and the
contexts that set it (PB T2, plan Task 4)."""

from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from django.test import RequestFactory

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.devices.auth import DeviceTokenAuth
from apps.documents.export import export_context
from apps.workspaces.context import (
    SYSTEM,
    current_actor,
    system_actor,
    workspace_context,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Workspace
from tests.members_support import admin_of, join, paired

APP_ROLE = "brievie_app"
HELPERS = ("public.brievie_actor_id()", "public.brievie_is_system()")


def _with_actor(value: str | None, expression: str) -> object:
    with transaction.atomic(), connection.cursor() as c:
        if value is not None:
            c.execute("SELECT set_config('brievie.actor', %s, true)", [value])
        c.execute(f"SELECT {expression}")
        row = c.fetchone()
    assert row is not None
    return row[0]


def test_actor_id_is_the_uuid_of_a_user_actor() -> None:
    user_id = uuid4()
    assert _with_actor(str(user_id), "public.brievie_actor_id()") == user_id


@pytest.mark.parametrize(
    "value", ["system", "", "SYSTEM", "not-a-uuid", "0000000-0000-0000-0000-000000000000"]
)
def test_actor_id_is_null_for_anything_else(value: str) -> None:
    assert _with_actor(value, "public.brievie_actor_id()") is None


def test_actor_id_is_null_without_the_setting() -> None:
    assert _with_actor(None, "public.brievie_actor_id()") is None


def test_is_system_only_for_the_exact_word() -> None:
    assert _with_actor("system", "public.brievie_is_system()") is True
    for value in ("System", "", str(uuid4())):
        assert _with_actor(value, "public.brievie_is_system()") is False
    assert _with_actor(None, "public.brievie_is_system()") is False


def _proc(signature: str, column: str) -> object:
    with connection.cursor() as c:
        c.execute(
            f"SELECT {column} FROM pg_proc WHERE oid = %s::regprocedure",  # noqa: S608
            [signature],
        )
        row = c.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("signature", HELPERS)
def test_helpers_pin_search_path_and_are_stable(signature: str) -> None:
    assert _proc(signature, "proconfig") == ['search_path=""']
    assert _proc(signature, "provolatile") == "s"
    assert _proc(signature, "prosecdef") is False


@pytest.mark.parametrize("signature", HELPERS)
def test_helpers_are_owned_by_the_schema_owner(signature: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT current_user")
        row = c.fetchone()
    assert row is not None
    assert _proc(signature, "pg_get_userbyid(proowner)") == row[0]


@pytest.mark.parametrize("signature", HELPERS)
def test_app_role_may_execute_the_helpers(signature: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", [APP_ROLE, signature])
        row = c.fetchone()
    assert row == (True,)


def test_actor_id_returns_a_uuid_type() -> None:
    value = _with_actor(str(UUID(int=7)), "public.brievie_actor_id()")
    assert isinstance(value, UUID)


def test_workspace_context_requires_an_actor(ws_a: Workspace) -> None:
    with pytest.raises(TypeError), workspace_context(ws_a.id):  # type: ignore[call-arg]
        pass


def test_workspace_context_refuses_an_unknown_actor(ws_a: Workspace) -> None:
    not_an_actor = str(uuid4())
    with (
        pytest.raises(ValueError, match="Not an actor"),
        workspace_context(ws_a.id, actor=not_an_actor),  # type: ignore[arg-type]
    ):
        pass


def test_context_sets_and_restores_the_actor(ws_a: Workspace) -> None:
    user = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert current_actor() == SYSTEM
        with workspace_context(ws_a.id, actor=user.id):
            assert current_actor() == user.id
        assert current_actor() == SYSTEM
    assert current_actor() is None


def test_actor_restored_after_an_exception(ws_a: Workspace) -> None:
    with pytest.raises(RuntimeError), workspace_context(ws_a.id, actor=admin_of(ws_a).id):
        raise RuntimeError
    assert current_actor() is None


def test_system_actor_sets_and_restores(ws_a: Workspace) -> None:
    user = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=user.id):
        with system_actor():
            assert current_actor() == SYSTEM
        assert current_actor() == user.id


def test_system_actor_needs_a_workspace() -> None:
    with pytest.raises(RuntimeError), system_actor():
        pass


def test_workspace_member_runs_as_the_session_user(ws_a: Workspace, rf: RequestFactory) -> None:
    user = admin_of(ws_a)
    request = rf.get("/")
    request.user = user
    with transaction.atomic():
        workspace_member(request, ws_a.id)
        assert current_actor() == user.id


def test_workspace_member_runs_as_the_device_user(ws_a: Workspace, rf: RequestFactory) -> None:
    user = join(ws_a)
    _device, token = paired(user)
    request = rf.get("/")
    assert DeviceTokenAuth().authenticate(request, token) == user
    with transaction.atomic():
        workspace_member(request, ws_a.id)
        assert current_actor() == user.id


def test_record_inside_a_user_context_restores_the_user(ws_a: Workspace) -> None:
    user = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=user.id):
        record(
            AuditCode.RULE_RUN,
            actor=AuditActor.for_user(user),
            target=AuditTarget(kind=TargetKind.RULE, id=uuid4()),
            workspace_id=ws_a.id,
        )
        assert current_actor() == user.id


def test_export_context_runs_as_the_reader_or_the_system(ws_a: Workspace) -> None:
    user = admin_of(ws_a)
    with export_context(ws_a.id, reader_id=user.id):
        assert current_actor() == user.id
    with export_context(ws_a.id, reader_id=None):
        assert current_actor() == SYSTEM
