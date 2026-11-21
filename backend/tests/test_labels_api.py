import logging
import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.labels.models import Label
from apps.labels.services import create_extra_labels
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of
from tests.factories import UserFactory
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response


def labels_url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/labels"


def create(client: Client, workspace_id: object, body: dict[str, object]) -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/labels", body)


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_list_labels_by_name_with_counts(client_a: Client, ws_a: Workspace) -> None:
    r = client_a.get(labels_url(ws_a.id))

    assert r.status_code == 200
    rows = r.json()
    assert [row["name"] for row in rows] == sorted((row["name"] for row in rows), key=str.lower)
    zorg = next(row for row in rows if row["name"] == "Zorg")
    assert set(zorg) == {"id", "name", "color", "system_key", "document_count", "department_id"}
    assert (zorg["color"], zorg["system_key"], zorg["document_count"]) == ("red", "zorg", 0)


def test_create_update_and_delete_a_label(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": " Hobby 111222333 "})
    assert r.status_code == 201
    hobby = r.json()
    assert (hobby["name"], hobby["color"], hobby["system_key"]) == ("Hobby [BSN]", "grey", "")

    r = client_a.put(
        f"{labels_url(ws_a.id)}/{hobby['id']}",
        {"name": "Sport", "color": "teal"},
        content_type="application/json",
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["color"]) == ("Sport", "teal")

    r = client_a.delete(f"{labels_url(ws_a.id)}/{hobby['id']}")
    assert r.status_code == 204
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not Label.objects.filter(id=hobby["id"]).exists()


@pytest.mark.parametrize("body", [{"name": "Gezondheid"}, {"name": "Gezondheid", "color": ""}])
def test_update_without_a_colour_keeps_the_colour(
    client_a: Client, ws_a: Workspace, body: dict[str, object]
) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        zorg = Label.objects.get(workspace=ws_a, system_key="zorg")

    r = client_a.put(f"{labels_url(ws_a.id)}/{zorg.id}", body, content_type="application/json")

    assert r.status_code == 200
    assert (r.json()["name"], r.json()["color"]) == ("Gezondheid", "red")


def test_label_name_unique_ignoring_case(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": "zorg"})

    assert r.status_code == 409
    assert r.json()["code"] == "label_exists"

    sport = create(client_a, ws_a.id, {"name": "Sport"}).json()
    r = client_a.put(
        f"{labels_url(ws_a.id)}/{sport['id']}",
        {"name": "BANK", "color": "grey"},
        content_type="application/json",
    )
    assert r.status_code == 409
    assert r.json()["code"] == "label_exists"


def test_unknown_color_is_422(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": "Sport", "color": "pink"})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_unknown_color_on_update_is_422_and_changes_nothing(
    client_a: Client, ws_a: Workspace
) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        zorg = Label.objects.get(workspace=ws_a, system_key="zorg")

    r = client_a.put(
        f"{labels_url(ws_a.id)}/{zorg.id}",
        {"name": "Gezondheid", "color": "pink"},
        content_type="application/json",
    )

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    with workspace_context(ws_a.id, actor=SYSTEM):
        zorg.refresh_from_db()
    assert (zorg.name, zorg.color) == ("Zorg", "red")


def test_blank_name_is_422(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": "   "})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_label_of_another_workspace_is_not_found(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id, actor=SYSTEM):
        stranger = Label.objects.get(workspace=ws_b, system_key="bank")

    for r in (
        client_a.put(
            f"{labels_url(ws_a.id)}/{stranger.id}",
            {"name": "X1", "color": "grey"},
            content_type="application/json",
        ),
        client_a.delete(f"{labels_url(ws_a.id)}/{stranger.id}"),
        client_a.delete(f"{labels_url(ws_a.id)}/{uuid.uuid4()}"),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "label_not_found"


def test_apply_preset_needs_admin(client: Client, ws_a: Workspace) -> None:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws_a, role="member")
    client.force_login(member)

    r = post_json(client, f"/workspaces/{ws_a.id}/labels/preset")

    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_apply_preset_returns_the_label_list(client_a: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        Label.objects.get(workspace=ws_a, system_key="wonen").delete()

    r = post_json(client_a, f"/workspaces/{ws_a.id}/labels/preset")

    assert r.status_code == 200
    assert len(r.json()) == 10
    assert "Wonen" in {row["name"] for row in r.json()}


def test_viewer_may_list_but_not_create(client: Client, ws_a: Workspace) -> None:
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    assert client.get(labels_url(ws_a.id)).status_code == 200
    assert create(client, ws_a.id, {"name": "Sport"}).status_code == 403


def test_extra_labels_that_only_postgresql_sees_as_equal_are_skipped(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    # Python lowers "İ" to "i" + a combining dot, PostgreSQL to a plain "i".
    with caplog.at_level(logging.DEBUG), workspace_context(ws_a.id, actor=SYSTEM):
        create_extra_labels(workspace=ws_a, names=["İstanbul", "istanbul", "Reizen"])
        names = set(Label.objects.filter(workspace=ws_a).values_list("name", flat=True))

    assert {"İstanbul", "Reizen"} <= names
    assert "istanbul" not in names
    assert "stanbul" not in caplog.text


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("​", "Een label heeft een naam nodig."),
        ("⠀", "Een label heeft een naam nodig."),
        (
            "TEST ‮​",
            "Een naam mag geen tekens bevatten die de leesrichting van de tekst omdraaien.",
        ),
    ],
)
def test_a_name_that_shows_nothing_or_turns_text_around_is_refused(
    client_a: Client, ws_a: Workspace, name: str, message: str
) -> None:
    r = create(client_a, ws_a.id, {"name": name})

    assert r.status_code == 422
    assert r.json() == {"code": "validation_error", "message": message}


def test_a_workspace_holds_a_limited_number_of_labels(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Security review 2026-11-20 (content L5): the AI sees only the first 100 labels.
    from apps.labels import services

    with workspace_context(ws_a.id, actor=SYSTEM):
        present = Label.objects.filter(workspace=ws_a).count()
    monkeypatch.setattr(services, "MAX_LABELS", present + 1)

    assert create(client_a, ws_a.id, {"name": "Nog een"}).status_code == 201
    r = create(client_a, ws_a.id, {"name": "Te veel"})

    assert r.status_code == 409
    assert r.json() == {
        "code": "too_many_labels",
        "message": "Deze werkruimte heeft het grootste aantal labels. Verwijder er eerst een.",
    }
