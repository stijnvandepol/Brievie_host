"""An assignment ends when sight of the letter does (spec S 5.3, ruling MT6; plan Task 9).

Every writer that can narrow who reads a letter calls the release. Today these are: the
end of `addressee_scope` (every change of a letter's addressees, readers or privacy goes
through it), the nightly check, demoting a member to viewer, removing a member, and
unlinking a person from a member. "Wie ziet wat" adds its own writers later and each must
appear here: T24 (private letters, shares), T27 (unlink, member removal), T37 (the
switches), T38 (sorters, department members).
"""

import ast
from pathlib import Path
from typing import Final

import pytest
from django.conf import LazySettings
from django.test import Client

from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentEvent
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.visibility_support import Office, as_actor, office

APPS: Final = Path(__file__).resolve().parent.parent / "apps"

# The modules whose narrowing writers release assignments, and the call that does it.
RELEASE_CALLERS: Final = {
    "documents/visibility.py",
    "workspaces/members.py",
    "people/services.py",
    "workspaces/access.py",
    # Every change of sight (switches, roles, sorters, departments, links) releases.
    "workspaces/visibility_settings.py",
}
RELEASES: Final = {"release_unseeing_assignees", "release_member", "_release_assignees"}
# What narrows sight: the reader rows are recomputed, or a role or a person link changes.
NARROWING_CALLS: Final = {"recompute", "recompute_where"}


@pytest.fixture(autouse=True)
def _public_url(settings: LazySettings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _assigned_to_femke(o: Office) -> Task:
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.get(document=o.letters["finance_letter"])
        Task.objects.filter(id=task.id).update(assigned_to=o.finance, assigned_by=o.finance)
    return task


def _assignee(o: Office, task: Task) -> object:
    with as_actor(o.workspace, SYSTEM):
        return Task.objects.get(id=task.id).assigned_to_id


def _system_releases(o: Office, task: Task) -> int:
    with as_actor(o.workspace, SYSTEM):
        return DocumentEvent.objects.filter(
            document__tasks__id=task.id, kind="task_unassigned", origin="system", actor=None
        ).count()


def test_hand_over_releases_assignee(client: Client) -> None:
    o = office()
    task = _assigned_to_femke(o)
    client.force_login(o.finance)
    letter = o.letters["finance_letter"]
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}/addressees",
        {"person_ids": [str(o.persons["HR"].id)]},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert _assignee(o, task) is None
    assert _system_releases(o, task) == 1


def test_hand_over_to_a_fellow_reader_keeps_the_assignee(client: Client) -> None:
    o = office()
    o.state(e=True, d=False, a=True)  # everyone reads everything
    task = _assigned_to_femke(o)
    client.force_login(o.finance)
    letter = o.letters["finance_letter"]
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}/addressees",
        {"person_ids": [str(o.persons["HR"].id)]},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert _assignee(o, task) == o.finance.id


def test_member_removal_releases(client: Client) -> None:
    o = office()
    task = _assigned_to_femke(o)
    client.force_login(o.admin)
    r = client.delete(f"/api/v1/workspaces/{o.workspace.id}/members/{o.finance.id}")
    assert r.status_code in (200, 204)
    assert _assignee(o, task) is None
    assert _system_releases(o, task) == 1


def test_demotion_to_viewer_releases(client: Client) -> None:
    o = office()
    task = _assigned_to_femke(o)
    client.force_login(o.admin)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/members/{o.finance.id}/role",
        {"role": "viewer"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert _assignee(o, task) is None
    assert _system_releases(o, task) == 1


def test_unlinking_a_person_releases(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.get(document=o.letters["own_piet"])
        Task.objects.filter(id=task.id).update(assigned_to=o.plain)
    # Only the linked member unlinks (PB T12): Piet himself.
    client.force_login(o.plain)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Piet'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert _assignee(o, task) is None


def test_nightly_check_releases_and_counts() -> None:
    o = office()
    task = _assigned_to_femke(o)
    letter = o.letters["finance_letter"]
    with as_actor(o.workspace, SYSTEM):
        # A change that skipped the scope: only the nightly check catches it.
        DocumentAddressee.objects.filter(document=letter).update(person=o.persons["HR"])
        Document.objects.filter(id=letter.id).update(addressee_ids=[o.persons["HR"].id])
        check = visibility.verify_visibility(o.workspace.id)
    assert check.assignments_released == 1
    assert _assignee(o, task) is None


def _calls(node: ast.AST) -> set[str]:
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                names.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                names.add(n.func.attr)
    return names


def _writes_role(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "role" for t in n.targets)
        for n in ast.walk(node)
    )


def _unlinks_a_person(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "user_id" for t in n.targets)
        and isinstance(n.value, ast.Name)
        for n in ast.walk(node)
    )


def test_every_narrowing_writer_releases() -> None:
    """A module with a function that recomputes readers, writes a member's role or links
    a person to another user is on the list, and that module calls a release."""
    found = set()
    for path in sorted(APPS.rglob("*.py")):
        relative = str(path.relative_to(APPS))
        if "/migrations/" in f"/{relative}" or relative.startswith("tasks/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            if function.name in NARROWING_CALLS:
                continue  # the definitions themselves
            calls = _calls(function)
            if (
                calls & NARROWING_CALLS
                or (_writes_role(function) and relative.startswith("workspaces/"))
                or (_unlinks_a_person(function) and relative.startswith("people/"))
            ):
                found.add(relative)
    assert found <= RELEASE_CALLERS, found - RELEASE_CALLERS
    for module in RELEASE_CALLERS:
        source = (APPS / module).read_text(encoding="utf-8")
        assert _calls(ast.parse(source)) & RELEASES, module
