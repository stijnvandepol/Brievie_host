""" "Wie kan mijn post zien?" and "Wie kan deze brief zien?" (plan Task 41, S 4.15)."""

import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents import visibility
from apps.documents.models import Document, DocumentReader
from apps.workspaces.context import SYSTEM
from tests.visibility_support import ALL_16_STATES, Office, as_actor, office


def _as(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def _access(o: Office, client: Client, name: str) -> dict[str, list[str]]:
    letter = o.letters[name]
    r = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}/access")
    assert r.status_code == 200, r.json()
    viewers = r.json()["viewers"]
    assert [v["name"] for v in viewers] == sorted((v["name"] for v in viewers), key=str.casefold)
    return {v["name"]: v["reasons"] for v in viewers}


def test_post_access_sentences_for_a_member_in_own_post(client: Client) -> None:
    o = office()
    r = _as(client, o.plain).get(f"/api/v1/workspaces/{o.workspace.id}/post-access")
    assert r.status_code == 200
    assert r.json()["sentences"] == [
        "Je post zien: jij en de anderen op wier naam de brief ook staat, de mensen van een "
        "afdeling als hij op naam van die afdeling staat, wie de brief scant (tot hij klaar "
        "is) en met wie hij gedeeld is.",
        "De beheerders lezen je post niet.",
        "Post zonder naam zien: Sandra (verdeelt post).",
        "Privé-post zien alleen: de mensen op wier naam hij staat, de mensen van een afdeling "
        "op wier naam hij staat, wie de brief scande of privé maakte, en met wie hij gedeeld "
        "is.",
        "Noodtoegang staat aan. De beheerders kunnen je post openen. Ze moeten een reden "
        "geven, en jij krijgt dan een melding.",
        "Brieven worden niet door AI gelezen.",
    ]


def test_post_access_names_the_server_admin(client: Client) -> None:
    o = office()
    User.objects.filter(id=o.admin.id).update(is_server_admin=True)
    sentences = (
        _as(client, o.viewer)
        .get(f"/api/v1/workspaces/{o.workspace.id}/post-access")
        .json()["sentences"]
    )
    assert "Wie de server zelf beheert, kan technisch bij alle gegevens. Dat is Anne." in (
        sentences
    )


def test_document_access_lists_viewers_with_reasons(client: Client) -> None:
    o = office()
    assert _access(o, _as(client, o.finance), "finance_letter") == {"Femke": ["afdeling Finance"]}
    assert _access(o, client, "scanned_in_review") == {
        "Femke": ["op naam van Femke"],
        "Piet": ["heeft de brief gescand"],
    }
    assert _access(o, client, "private_femke") == {"Femke": ["is gedeeld"]}
    assert _access(o, _as(client, o.sorter), "only_directie") == {"Sandra": ["verdeelt post"]}
    o.state(e=True, d=True, a=True)
    reasons = _access(o, client, "finance_letter")
    assert reasons["Piet"] == ["ziet alle post"]
    assert reasons["Femke"] == ["ziet alle post", "afdeling Finance"]


def test_emergency_access_shows_until_when(client: Client) -> None:
    o = office()
    until = timezone.now() + timedelta(minutes=30)
    with as_actor(o.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=o.workspace,
            document=o.letters["finance_letter"],
            user=o.admin,
            reason=DocumentReader.Reason.EMERGENCY,
            expires_at=until,
        )
    reasons = _access(o, _as(client, o.admin), "finance_letter")
    [line] = reasons["Anne"]
    assert line.startswith("noodtoegang tot ")
    assert len(line.removeprefix("noodtoegang tot ")) == 5


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_every_viewer_has_a_reason_and_nobody_else_is_listed(state: dict[str, bool]) -> None:
    o = office()
    o.state(**state)
    for name, letter in o.letters.items():
        with as_actor(o.workspace, SYSTEM):
            reasons = visibility.viewer_reasons(Document.objects.get(id=letter.id))
        sight = {u.id for u in o.users.values() if name in o.visible(u.id)}
        assert set(reasons) == sight, (state, name)
        for user_id, why in reasons.items():
            assert why, (state, name, user_id)


def test_document_access_of_a_hidden_letter_is_404(client: Client) -> None:
    o = office()
    _as(client, o.plain)
    ws = f"/api/v1/workspaces/{o.workspace.id}/documents"
    hidden = client.get(f"{ws}/{o.letters['finance_letter'].id}/access")
    unknown = client.get(f"{ws}/{uuid.uuid4()}/access")
    assert hidden.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "document_not_found"
