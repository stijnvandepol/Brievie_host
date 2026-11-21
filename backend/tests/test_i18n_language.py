"""Which language the server answers in: Accept-Language, unless the account chose."""

import uuid

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from apps.labels.models import Label
from apps.rules.models import Rule
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.factories import UserFactory
from tests.helpers import SETUP_TOKEN, post_json
from tests.members_support import as_user, bearer, paired

NOT_SIGNED_IN = {"nl": "Je bent niet ingelogd.", "en": "You are not signed in."}
NO_WORKSPACE = {
    "nl": "We kunnen deze werkruimte niet vinden.",
    "en": "We can't find this workspace.",
}


def missing_workspace(
    client: Client, header: str | None = None, token: str = ""
) -> tuple[str, str]:
    """The 404 message and Content-Language for a workspace that does not exist."""
    headers = {"Accept-Language": header} if header else {}
    if token:
        headers.update(bearer(token))
    r = client.get(f"/api/v1/workspaces/{uuid.uuid4()}/members", headers=headers)
    assert r.status_code == 404
    return r.json()["message"], r.headers["Content-Language"]


@pytest.mark.parametrize(
    ("header", "language"),
    [
        (None, "nl"),
        ("en", "en"),
        ("en-GB,en;q=0.9,nl;q=0.5", "en"),
        ("nl-NL,nl;q=0.9,en;q=0.8", "nl"),
        ("de-DE,de;q=0.9", "nl"),
    ],
)
def test_before_sign_in_accept_language_decides(header: str | None, language: str) -> None:
    headers = {"Accept-Language": header} if header else {}

    r = Client().get("/api/v1/me", headers=headers)

    assert r.status_code == 401
    assert r.json() == {"code": "not_authenticated", "message": NOT_SIGNED_IN[language]}
    assert r.headers["Content-Language"] == language


@pytest.mark.parametrize(
    ("chosen", "header", "language"),
    [
        ("", None, "nl"),
        ("", "en", "en"),
        ("en", None, "en"),
        ("en", "nl", "en"),
        ("nl", "en", "nl"),
    ],
)
def test_a_session_answers_in_the_account_choice_else_the_header(
    chosen: str, header: str | None, language: str
) -> None:
    client = as_user(UserFactory.create(language=chosen))

    assert missing_workspace(client, header) == (NO_WORKSPACE[language], language)


@pytest.mark.parametrize(
    ("chosen", "header", "language"),
    [("", None, "nl"), ("", "en", "en"), ("en", None, "en"), ("nl", "en", "nl")],
)
def test_a_device_answers_in_the_account_choice_else_the_header(
    chosen: str, header: str | None, language: str
) -> None:
    _, token = paired(UserFactory.create(language=chosen))

    assert missing_workspace(Client(), header, token) == (NO_WORKSPACE[language], language)


def test_the_account_stores_its_choice_and_the_next_request_follows_it() -> None:
    user = UserFactory.create()
    client = as_user(user)

    r = client.put("/api/v1/me/language", {"language": "en"}, content_type="application/json")

    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert User.objects.get(id=user.id).language == "en"
    assert missing_workspace(client, "nl")[0] == NO_WORKSPACE["en"]

    r = client.put("/api/v1/me/language", {"language": ""}, content_type="application/json")

    assert r.json()["language"] == ""
    assert missing_workspace(client)[0] == NO_WORKSPACE["nl"]


def test_an_unknown_language_is_refused() -> None:
    user = UserFactory.create()

    r = as_user(user).put(
        "/api/v1/me/language", {"language": "de"}, content_type="application/json"
    )

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert User.objects.get(id=user.id).language == ""


def test_a_device_may_store_the_choice_too() -> None:
    user = UserFactory.create()
    _, token = paired(user)

    r = Client().put(
        "/api/v1/me/language",
        {"language": "en"},
        content_type="application/json",
        headers=bearer(token),
    )

    assert r.status_code == 200
    assert User.objects.get(id=user.id).language == "en"


@pytest.mark.parametrize(
    ("header", "label", "rule"),
    [("nl", "Zorg", "Label Zorg"), ("en", "Healthcare", "Label Healthcare")],
)
def test_the_start_set_is_made_in_the_language_of_the_setup(
    settings: Settings, header: str, label: str, rule: str
) -> None:
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN
    body = {
        "setup_token": SETUP_TOKEN,
        "email": "beheer@example.nl",
        "password": "een-lang-wachtwoord-2026",
        "name": "Anna de Vries",
        "workspace_name": "Thuis",
        "workspace_kind": "home",
    }

    r = post_json(Client(), "/setup", body, headers={"Accept-Language": header})

    assert r.status_code == 201
    workspace = Workspace.objects.get()
    with workspace_context(workspace.id, actor=SYSTEM):
        care = Label.objects.get(system_key="zorg")
        assert care.name == label
        assert Rule.objects.get(system_key="zorg").name == rule
