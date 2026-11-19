import runpy
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import DataError, connection, transaction
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app
from pytest_django import Settings

import brievie.settings
from apps.audit import jobs
from apps.audit.codes import AuditCode
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.services import AuditActor, AuditTarget, record
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from apps.workspaces.rls import SETTING_NAME

FUNCTIONS = [
    "brievie_purge_workspace_audit(uuid, timestamptz)",
    "brievie_purge_server_audit(timestamptz)",
]


def workspace_row(workspace: Workspace, *, age_days: int) -> uuid.UUID:
    """A workspace audit row made `age_days` ago (aged as the owner, who may update)."""
    target_id = uuid.uuid4()
    record(
        AuditCode.RULE_DELETED,
        actor=AuditActor.system(),
        target=AuditTarget(kind="rule", id=target_id),
        workspace_id=workspace.id,
    )
    with workspace_context(workspace.id):
        WorkspaceAuditEvent.objects.filter(target_id=target_id).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
    return target_id


def server_row(*, age_days: int) -> uuid.UUID:
    target_id = uuid.uuid4()
    record(
        AuditCode.DEVICE_REVOKED,
        actor=AuditActor.system(),
        target=AuditTarget(kind="device", id=target_id),
    )
    ServerAuditEvent.objects.filter(target_id=target_id).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return target_id


def remaining(workspace: Workspace) -> set[uuid.UUID | None]:
    with workspace_context(workspace.id):
        return set(WorkspaceAuditEvent.objects.values_list("target_id", flat=True))


def purge_workspace(workspace_id: uuid.UUID, cutoff: datetime) -> int:
    with connection.cursor() as c:
        c.execute("SELECT brievie_purge_workspace_audit(%s, %s)", [workspace_id, cutoff])
        row = c.fetchone()
    assert row is not None
    return int(row[0])


def purge_server(cutoff: datetime) -> int:
    with connection.cursor() as c:
        c.execute("SELECT brievie_purge_server_audit(%s)", [cutoff])
        row = c.fetchone()
    assert row is not None
    return int(row[0])


def current_workspace_setting() -> str:
    with connection.cursor() as c:
        c.execute("SELECT COALESCE(current_setting(%s, true), '')", [SETTING_NAME])
        row = c.fetchone()
    assert row is not None
    return str(row[0])


def test_purge_refuses_a_cut_off_younger_than_90_days(ws_a: Workspace) -> None:
    old = workspace_row(ws_a, age_days=400)
    server_old = server_row(age_days=400)
    cutoff = timezone.now() - timedelta(days=89)

    with pytest.raises(DataError), transaction.atomic():
        purge_workspace(ws_a.id, cutoff)
    with pytest.raises(DataError), transaction.atomic():
        purge_server(cutoff)

    assert remaining(ws_a) == {old}
    assert set(ServerAuditEvent.objects.values_list("target_id", flat=True)) == {server_old}


def test_purge_removes_only_older_rows_of_that_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    a_old = workspace_row(ws_a, age_days=400)
    a_new = workspace_row(ws_a, age_days=10)
    b_old = workspace_row(ws_b, age_days=400)
    b_new = workspace_row(ws_b, age_days=10)

    assert purge_workspace(ws_a.id, timezone.now() - timedelta(days=365)) == 1

    assert a_old not in remaining(ws_a)
    assert remaining(ws_a) == {a_new}
    assert remaining(ws_b) == {b_old, b_new}


def test_purge_restores_the_previous_workspace_setting(ws_a: Workspace, ws_b: Workspace) -> None:
    workspace_row(ws_a, age_days=400)

    with workspace_context(ws_b.id):
        assert purge_workspace(ws_a.id, timezone.now() - timedelta(days=365)) == 1
        assert current_workspace_setting() == str(ws_b.id)
    assert current_workspace_setting() == ""


def test_server_purge_removes_rows_older_than_the_cut_off() -> None:
    old = server_row(age_days=400)
    new = server_row(age_days=100)

    assert purge_server(timezone.now() - timedelta(days=365)) == 1

    assert set(ServerAuditEvent.objects.values_list("target_id", flat=True)) == {new}
    assert old not in set(ServerAuditEvent.objects.values_list("target_id", flat=True))


@pytest.mark.parametrize("function", FUNCTIONS)
def test_app_role_may_execute_the_purge_functions_and_public_may_not(function: str) -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT has_function_privilege('brievie_app', %s, 'EXECUTE'),"
            " has_function_privilege('public', %s, 'EXECUTE')",
            [function, function],
        )
        assert c.fetchone() == (True, False)
        c.execute(
            "SELECT p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig"
            " FROM pg_proc p WHERE p.oid = %s::regprocedure",
            [function],
        )
        row = c.fetchone()
        c.execute("SELECT current_user")
        owner = c.fetchone()
    assert row is not None
    assert owner is not None
    security_definer, function_owner, config = row
    assert security_definer is True
    # The suite runs as the schema owner, which also ran the migration.
    assert function_owner == owner[0]
    # pg_temp last, so a temporary object can never shadow a table or function.
    assert config == ["search_path=pg_catalog, public, pg_temp"]


def test_purge_job_uses_the_retention_setting(settings: Settings, ws_a: Workspace) -> None:
    settings.BRIEVIE_AUDIT_RETENTION_DAYS = 120
    gone = workspace_row(ws_a, age_days=130)
    kept = workspace_row(ws_a, age_days=100)
    server_gone = server_row(age_days=130)
    server_kept = server_row(age_days=100)

    jobs.purge_audit_events(timestamp=0)

    assert remaining(ws_a) == {kept}
    assert gone not in remaining(ws_a)
    assert set(ServerAuditEvent.objects.values_list("target_id", flat=True)) == {server_kept}
    assert server_gone not in set(ServerAuditEvent.objects.values_list("target_id", flat=True))


def test_purge_job_with_the_shortest_retention_is_not_refused(
    settings: Settings, ws_a: Workspace
) -> None:
    """The job's cut-off comes from the database's clock, so 90 days is not 'too young'."""
    settings.BRIEVIE_AUDIT_RETENTION_DAYS = 90
    gone = workspace_row(ws_a, age_days=91)
    kept = workspace_row(ws_a, age_days=89)

    jobs.purge_audit_events(timestamp=0)

    assert remaining(ws_a) == {kept}
    assert gone not in remaining(ws_a)


def test_retention_defaults_to_a_year(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIEVIE_AUDIT_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    values = runpy.run_path(str(Path(brievie.settings.__file__)))
    assert values["BRIEVIE_AUDIT_RETENTION_DAYS"] == 365


def test_retention_below_90_days_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_AUDIT_RETENTION_DAYS", "89")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    with pytest.raises(ImproperlyConfigured, match="BRIEVIE_AUDIT_RETENTION_DAYS"):
        runpy.run_path(str(Path(brievie.settings.__file__)))


def test_purge_task_is_registered_nightly_with_a_queueing_lock() -> None:
    task = procrastinate_app.tasks["purge_audit_events"]
    assert task.queue == "pipeline"
    # Every worker defers every periodic task; the lock keeps at most one waiting.
    assert task.queueing_lock == "purge_audit_events"
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["purge_audit_events"] == "17 3 * * *"
