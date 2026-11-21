"""Thuis and Organisatie set the switches at installation, and on the command line (plan
Task 40, S 4.2, 5.2, R40, R41)."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from pytest_django import Settings

from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.labels.models import Label
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.invitations import lookup_invitation
from apps.workspaces.models import Invitation, Membership, Workspace
from apps.workspaces.presets import PRESETS
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.helpers import SETUP_TOKEN, post_json
from tests.test_accounts_api import SETUP_BODY

SIX = (
    "everyone_sees_all",
    "departments_enabled",
    "admins_read_all",
    "care_is_private",
    "emergency_access",
    "allow_member_export",
)


def _values(workspace: Workspace) -> tuple[bool, ...]:
    row = Workspace.objects.get(id=workspace.id)
    return tuple(getattr(row, name) for name in SIX)


def test_thuis_preset() -> None:
    workspace = create_workspace(name="Thuis", kind="home", admin=UserFactory.create())
    # E on, D off, A on, care on, emergency off, export on.
    assert _values(workspace) == (True, False, True, True, False, True)
    assert PRESETS["home"].everyone_sees_all is True


def test_organisatie_preset() -> None:
    workspace = create_workspace(name="Bakkerij", kind="business", admin=UserFactory.create())
    # E off, D on, A off, care off, emergency on, export off.
    assert _values(workspace) == (False, True, False, False, True, False)


@pytest.fixture
def _setup_token(settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN


SETUP: dict[str, object] = {
    **SETUP_BODY,
    "workspace_name": "Bakkerij Jansen",
    "workspace_kind": "business",
}


@pytest.mark.usefixtures("_setup_token")
def test_setup_of_an_organisation_starts_in_own_post(client: Client) -> None:
    r = post_json(client, "/setup", SETUP)
    assert r.status_code == 201, r.json()
    workspace = Workspace.objects.get(name="Bakkerij Jansen")
    assert _values(workspace) == (False, True, False, False, True, False)
    body = client.get(f"/api/v1/workspaces/{workspace.id}/visibility").json()
    assert body["summary"][:4] == [
        "Ieder ziet zijn eigen post en die van zijn afdelingen.",
        "Er zijn nog geen afdelingen.",
        "Post zonder naam zie jij, tot je iemand aanwijst die post verdeelt.",
        "Beheerders lezen geen post die niet voor hen is.",
    ]


@pytest.mark.usefixtures("_setup_token")
def test_setup_still_accepts_extra_labels(client: Client) -> None:
    r = post_json(client, "/setup", {**SETUP, "extra_labels": ["Sales"]})
    assert r.status_code == 201, r.json()
    workspace = Workspace.objects.get(name="Bakkerij Jansen")
    with workspace_context(workspace.id, actor=SYSTEM):
        assert Label.objects.filter(workspace=workspace, name="Sales").exists()


def _command(*args: str) -> str:
    out = io.StringIO()
    call_command("create_workspace", *args, stdout=out)
    return out.getvalue()


def test_create_workspace_command_with_an_existing_account() -> None:
    user = UserFactory.create(name="Erik", email="erik@example.nl")
    out = _command("--name", "Praktijk", "--kind", "business", "--admin-email", "Erik@Example.nl")
    workspace = Workspace.objects.get(name="Praktijk")
    assert Membership.objects.get(workspace=workspace, user=user).role == "admin"
    assert _values(workspace) == (False, True, False, False, True, False)
    assert "Erik is beheerder" in out
    assert not Invitation.objects.filter(workspace=workspace).exists()


def test_create_workspace_command_prints_an_invitation_once(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.example.nl"]
    out = _command("--name", "Vereniging", "--kind", "home", "--admin-email", "nieuw@example.nl")
    workspace = Workspace.objects.get(name="Vereniging")
    assert not Membership.objects.filter(workspace=workspace).exists()
    invitation = Invitation.objects.get(workspace=workspace)
    assert (invitation.role, invitation.email, invitation.created_by_id) == (
        "admin",
        "nieuw@example.nl",
        None,
    )
    assert invitation.created_by_name == "Serverbeheerder"
    [link] = [line for line in out.splitlines() if line.startswith("https://")]
    assert link.startswith("https://brievie.example.nl/uitnodiging#")
    token = link.split("#", 1)[1]
    # Only the hash is stored; the link opens the invitation.
    assert token not in str(Invitation.objects.values_list("token_hash", flat=True)[0])
    info = lookup_invitation(token=token)
    assert (info.workspace_name, info.role, info.invited_by) == (
        "Vereniging",
        "admin",
        "Serverbeheerder",
    )
    # Once the workspace has an admin, a maker-less invitation is dead.
    Membership.objects.create(workspace=workspace, user=UserFactory.create(), role="admin")
    with pytest.raises(Exception, match="werkt niet meer"):
        lookup_invitation(token=token)


def test_create_workspace_command_refuses_an_unknown_kind() -> None:
    with pytest.raises(CommandError) as raised:
        _command("--name", "School", "--kind", "school", "--admin-email", "a@example.nl")
    assert raised.value.returncode == 2
    assert "home (Thuis) of business (Organisatie)" in str(raised.value)
    assert not Workspace.objects.filter(name="School").exists()


def test_create_workspace_command_audits() -> None:
    _command("--name", "Kantoor", "--kind", "business", "--admin-email", "x@example.nl")
    workspace = Workspace.objects.get(name="Kantoor")
    row = ServerAuditEvent.objects.get(code="workspace_created")
    assert (row.actor_kind, row.target_id, row.target_name, row.values) == (
        "system",
        workspace.id,
        "Kantoor",
        {"kind": "business"},
    )
    with workspace_context(workspace.id, actor=SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(
            workspace_id=workspace.id, code="invitation_created"
        ).exists()
