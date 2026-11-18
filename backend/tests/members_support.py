"""Helpers for the tests of member changes: roles, removal and leaving."""

from django.test import Client

from apps.accounts.models import User
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory


def admin_of(ws: Workspace) -> User:
    """The workspace's first admin (the one `create_workspace` made)."""
    return (
        Membership.objects.filter(workspace=ws, role=Membership.Role.ADMIN)
        .select_related("user")
        .order_by("created_at", "id")[0]
        .user
    )


def join(ws: Workspace, role: str = Membership.Role.MEMBER, user: User | None = None) -> User:
    user = user or UserFactory.create()
    Membership.objects.create(user=user, workspace=ws, role=role)
    return user


def as_user(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def paired(user: User, name: str = "iPhone") -> tuple[Device, str]:
    _, code = create_pairing_code(user)
    return pair_device(code=code, device_name=name)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def role_of(ws: Workspace, user: User) -> str | None:
    return Membership.objects.filter(workspace=ws, user=user).values_list("role", flat=True).first()


def workspace_rows(ws: Workspace, code: str) -> list[WorkspaceAuditEvent]:
    with workspace_context(ws.id):
        return list(WorkspaceAuditEvent.objects.filter(code=code).order_by("created_at", "id"))


def server_rows(code: str) -> list[ServerAuditEvent]:
    return list(ServerAuditEvent.objects.filter(code=code).order_by("created_at", "id"))
