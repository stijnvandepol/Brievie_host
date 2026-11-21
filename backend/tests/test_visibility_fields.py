"""The switches, the sorting flag, departments and the task maker as fields (plan Task 6)."""

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import connection
from django.test import Client

from apps.people.models import DepartmentMember, Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.documents_support import admin_of, make_document
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.members_support import join

migration = importlib.import_module("apps.workspaces.migrations.0006_visibility_switches")


def _switches(workspace: Workspace) -> tuple[bool, bool, bool]:
    workspace.refresh_from_db()
    return (workspace.everyone_sees_all, workspace.departments_enabled, workspace.admins_read_all)


def test_existing_workspaces_migrate_to_everyone_sees_all(ws_a: Workspace, ws_b: Workspace) -> None:
    Workspace.objects.update(
        everyone_sees_all=False,
        departments_enabled=True,
        admins_read_all=False,
        care_is_private=False,
        emergency_access=False,
    )

    migration.visibility_defaults(global_apps, connection.schema_editor())

    for workspace in (ws_a, ws_b):
        assert _switches(workspace) == (True, False, True)
    assert (ws_a.care_is_private, ws_a.emergency_access) == (True, False)
    assert (ws_b.care_is_private, ws_b.emergency_access) == (False, True)


@pytest.mark.parametrize(
    ("kind", "switches"), [("home", (True, False, True)), ("business", (False, True, False))]
)
def test_new_workspaces_start_with_their_kinds_switches(
    kind: str, switches: tuple[bool, bool, bool]
) -> None:
    # P8 ended with the presets (plan Task 40): an Organisatie starts in own post.
    workspace = create_workspace(name="Nieuw", kind=kind, admin=UserFactory.create())
    assert _switches(workspace) == switches


def test_privacy_defaults_per_kind(ws_a: Workspace, ws_b: Workspace) -> None:
    assert (ws_a.kind, ws_a.care_is_private, ws_a.emergency_access) == ("home", True, False)
    assert (ws_b.kind, ws_b.care_is_private, ws_b.emergency_access) == ("business", False, True)
    assert not Membership.objects.filter(sorts_post=True).exists()


def _department(workspace: Workspace, name: str = "Financien") -> Person:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Person.objects.create(workspace=workspace, display_name=name, kind="department")


def test_department_cannot_have_an_account(ws_a: Workspace) -> None:
    department = _department(ws_a)
    department.user = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM), pytest.raises(ValueError, match="department"):
        department.save()


def test_department_member_must_be_a_member_and_a_department(ws_a: Workspace) -> None:
    department = _department(ws_a)
    member = join(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(workspace=ws_a, display_name="Jan")
        with pytest.raises(ValueError, match="department"):
            DepartmentMember.objects.create(workspace=ws_a, department=person, user=member)
        with pytest.raises(ValueError, match="member"):
            DepartmentMember.objects.create(
                workspace=ws_a, department=department, user=UserFactory.create()
            )
        row = DepartmentMember.objects.create(workspace=ws_a, department=department, user=member)
    assert row.is_lead is False


def test_department_member_table_has_forced_rls(ws_a: Workspace, ws_b: Workspace) -> None:
    department = _department(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        DepartmentMember.objects.create(workspace=ws_a, department=department, user=join(ws_a))
    with connection.cursor() as c:
        c.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = 'people_departmentmember'::regclass"
        )
        assert c.fetchone() == (True, True)
    with workspace_context(ws_b.id, actor=SYSTEM):
        assert not DepartmentMember.objects.exists()


def test_confirm_records_the_task_maker(client: Client, ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client.force_login(admin)
    document_id = make_document(ws_a, status="needs_review").id

    response = post_json(
        client,
        f"/workspaces/{ws_a.id}/documents/{document_id}/confirm",
        {
            "sender": "Gemeente",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [
                {
                    "type": "pay",
                    "description": "Betaal de aanslag",
                    "deadline": "2026-12-01",
                    "deadline_source": "manual",
                }
            ],
        },
    )

    assert response.status_code == 200, response.json()
    with workspace_context(ws_a.id, actor=SYSTEM):
        (task,) = Task.objects.filter(document_id=document_id)
    assert task.created_by_id == admin.id
    assert task.created_by_ai is False


def test_business_kind_reads_organisatie() -> None:
    assert str(Workspace.Kind.BUSINESS.label) == "Organisatie"
    assert str(Membership.Role.VIEWER.label) == "Alleen lezen"
