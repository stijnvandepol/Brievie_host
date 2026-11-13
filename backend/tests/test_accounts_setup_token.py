import threading
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import SetupToken, User
from apps.accounts.services import prepare_setup_token, sha256_hex
from apps.workspaces.models import Workspace
from tests.factories import UserFactory
from tests.helpers import post_json

BODY: dict[str, object] = {
    "email": "beheer@example.nl",
    "password": "een-lang-wachtwoord-2026",
    "name": "Anna de Vries",
    "workspace_name": "Thuis",
    "workspace_kind": "home",
}


@pytest.fixture(autouse=True)
def _no_env_token(settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = ""


def generated_token() -> str:
    out = StringIO()
    call_command("prepare_setup_token", stdout=out)
    lines = [line for line in out.getvalue().splitlines() if line.startswith("Installatiecode: ")]
    assert len(lines) == 1, out.getvalue()
    return lines[0].removeprefix("Installatiecode: ")


def test_correct_generated_token_sets_up_the_server() -> None:
    token = generated_token()

    r = post_json(Client(), "/setup", {**BODY, "setup_token": token})

    assert r.status_code == 201
    assert User.objects.count() == 1


@pytest.mark.parametrize("token", [None, "", "fout-token"])
def test_wrong_or_missing_token_is_403_and_creates_nothing(token: str | None) -> None:
    generated_token()
    body = dict(BODY) if token is None else {**BODY, "setup_token": token}

    r = post_json(Client(), "/setup", body)

    assert r.status_code == 403
    assert r.json()["code"] == "setup_token_invalid"
    assert User.objects.count() == 0
    assert Workspace.objects.count() == 0


def test_without_any_token_setup_is_impossible() -> None:
    r = post_json(Client(), "/setup", {**BODY, "setup_token": ""})
    assert r.status_code == 403


def test_token_is_deleted_and_unusable_after_setup() -> None:
    token = generated_token()
    assert post_json(Client(), "/setup", {**BODY, "setup_token": token}).status_code == 201

    assert SetupToken.objects.count() == 0
    r = post_json(Client(), "/setup", {**BODY, "email": "b@example.nl", "setup_token": token})
    assert r.status_code == 409
    assert User.objects.count() == 1


def test_env_token_is_used_and_nothing_is_generated(settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = "uit-de-omgeving-123"
    out = StringIO()
    call_command("prepare_setup_token", stdout=out)

    assert "Installatiecode: " not in out.getvalue()
    assert SetupToken.objects.count() == 0
    assert post_json(Client(), "/setup", {**BODY, "setup_token": "fout"}).status_code == 403
    r = post_json(Client(), "/setup", {**BODY, "setup_token": "uit-de-omgeving-123"})
    assert r.status_code == 201


def test_generated_token_is_stored_only_as_a_hash() -> None:
    token = generated_token()

    stored = SetupToken.objects.get()
    assert stored.token_hash == sha256_hex(token)
    with connection.cursor() as c:
        for table in connection.introspection.table_names():
            c.execute(f'SELECT * FROM "{table}"')  # noqa: S608 - names from introspection
            for row in c.fetchall():
                assert not any(token in str(value) for value in row), table


def test_token_is_generated_once_and_printed_once() -> None:
    first = generated_token()
    out = StringIO()
    call_command("prepare_setup_token", stdout=out)

    assert first not in out.getvalue()
    assert "Installatiecode: " not in out.getvalue()
    assert SetupToken.objects.get().token_hash == sha256_hex(first)


def test_new_flag_replaces_the_token() -> None:
    first = generated_token()
    out = StringIO()
    call_command("prepare_setup_token", "--new", stdout=out)
    second = out.getvalue().split("Installatiecode: ")[1].split()[0]

    assert second != first
    assert post_json(Client(), "/setup", {**BODY, "setup_token": first}).status_code == 403
    assert post_json(Client(), "/setup", {**BODY, "setup_token": second}).status_code == 201


def test_nothing_is_generated_once_a_user_exists() -> None:
    UserFactory.create()
    out = StringIO()
    call_command("prepare_setup_token", stdout=out)

    assert "Installatiecode: " not in out.getvalue()
    assert SetupToken.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_preparation_creates_one_token() -> None:
    barrier = threading.Barrier(2)
    printed: list[str | None] = []

    def run() -> None:
        try:
            barrier.wait(timeout=10)
            printed.append(prepare_setup_token())
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(p is None for p in printed) == [False, True]
    assert SetupToken.objects.count() == 1


def test_setup_attempts_are_rate_limited() -> None:
    generated_token()
    client = Client(REMOTE_ADDR="198.51.100.20")
    for _ in range(10):
        assert post_json(client, "/setup", {**BODY, "setup_token": "fout"}).status_code == 403

    r = post_json(client, "/setup", {**BODY, "setup_token": "fout"})

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"


def test_get_setup_reveals_nothing_about_the_token() -> None:
    generated_token()
    assert Client().get("/api/v1/setup").json() == {"needs_setup": True}
