""" "Wie kan mijn post zien?" is true in every state (security review of PR #39,
finding 5): every member `viewers()` lists for a member's letter is someone one of the
member's sentences names, also the scanner until the letter is done, a share, another
addressee, and a department's members on a private letter."""

import uuid

import pytest

from apps.documents import visibility
from apps.documents.models import Document, DocumentShare
from apps.people.models import DepartmentMember, Person
from apps.workspaces import visibility_settings
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.visibility_support import ALL_16_STATES, Office, as_actor, letter, office, private_letter

EVERYONE = "Iedereen in Brievie ziet alle post"
ADMINS_READ = "lezen alle post die niet privé is"
OWN = "Je post zien: jij en de anderen op wier naam de brief ook staat"
OWN_DEPARTMENTS = "de mensen van een afdeling als hij op naam van die afdeling staat"
SCANS = "wie de brief scant (tot hij klaar is)"
SHARED = "met wie hij gedeeld is"
PRIVATE = "Privé-post zien alleen: de mensen op wier naam hij staat"
PRIVATE_DEPARTMENTS = "de mensen van een afdeling op wier naam hij staat"
PRIVATE_SCANNER = "wie de brief scande of privé maakte"


def _says(lines: list[str], part: str) -> bool:
    return any(part in line for line in lines)


def _allowed(o: Office, document: Document, lines: list[str]) -> set[uuid.UUID]:
    """Everyone the sentences say may see this letter of the reader's."""
    with as_actor(o.workspace, SYSTEM):
        on = set(document.addressee_ids)
        accounts = set(
            Person.objects.filter(id__in=on, user__isnull=False).values_list("user_id", flat=True)
        )
        departments = set(
            DepartmentMember.objects.filter(department_id__in=on).values_list("user_id", flat=True)
        )
        shares = set(
            DocumentShare.objects.filter(document=document).values_list("user_id", flat=True)
        )
    members = set(
        Membership.objects.filter(workspace=o.workspace).values_list("user_id", flat=True)
    )
    admins = set(
        Membership.objects.filter(workspace=o.workspace, role="admin").values_list(
            "user_id", flat=True
        )
    )
    allowed: set[uuid.UUID] = set()
    if document.visibility == Document.Visibility.RESTRICTED:
        assert _says(lines, PRIVATE)
        allowed |= accounts | shares
        if _says(lines, PRIVATE_DEPARTMENTS):
            allowed |= departments
        if _says(lines, PRIVATE_SCANNER) and document.scanned_by_id is not None:
            allowed.add(document.scanned_by_id)
        return allowed
    if _says(lines, EVERYONE):
        return members
    if _says(lines, ADMINS_READ):
        allowed |= admins
    assert _says(lines, OWN)
    allowed |= accounts
    if _says(lines, OWN_DEPARTMENTS):
        allowed |= departments
    if _says(lines, SCANS) and document.scanned_by_id is not None and document.status != "done":
        allowed.add(document.scanned_by_id)
    if _says(lines, SHARED):
        allowed |= shares
    return allowed


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_every_viewer_of_my_post_is_named_by_my_sentences(state: dict[str, bool]) -> None:
    o = office()
    piet, femke = o.persons["Piet"], o.persons["Femke"]
    letters = [
        # Piet's own letter, also on Femke and on Finance, scanned by Hans and not done.
        letter(
            o.workspace,
            on=[piet, femke, o.persons["Finance"]],
            scanned_by=o.hr,
            status="needs_review",
        ),
        # Shared with Vera.
        letter(o.workspace, on=[piet]),
        # Private, on Finance (whose members read it), scanned by Hans, shared with Vera.
        private_letter(
            o.workspace, scanned_by=o.hr, addressees=[o.persons["Finance"]], shared_with=[o.viewer]
        ),
    ]
    with as_actor(o.workspace, SYSTEM):
        DocumentShare.objects.create(workspace=o.workspace, document=letters[1], user=o.viewer)
        visibility.recompute(letters[1])
    o.state(**state)
    for reader in (o.plain, o.finance):
        membership = Membership.objects.get(workspace=o.workspace, user=reader)
        lines = visibility_settings.post_access(membership)
        for document in letters:
            with as_actor(o.workspace, SYSTEM):
                fresh = Document.objects.get(id=document.id)
                seen = visibility.viewers([fresh.id])[fresh.id]
            on_reader = bool(set(fresh.addressee_ids) & _post_ids(o, reader.id))
            if not on_reader:
                continue
            assert seen <= _allowed(o, fresh, lines), (state, reader.name, lines)


def _post_ids(o: Office, user_id: uuid.UUID) -> set[uuid.UUID]:
    """The reader's post: their persons and departments (whatever the switch)."""
    with as_actor(o.workspace, SYSTEM):
        return set(Person.objects.filter(user_id=user_id).values_list("id", flat=True)) | set(
            DepartmentMember.objects.filter(user_id=user_id).values_list("department_id", flat=True)
        )
