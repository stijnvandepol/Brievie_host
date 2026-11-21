import logging
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.pipeline import settings_service
from apps.pipeline.models import AIConsentEvent, AISettings
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for
from tests.factories import UserFactory
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response

API_KEY = "nep-sleutel-voor-tests"
MISTRAL = "https://api.mistral.ai/v1"
CLAUDE = "https://api.anthropic.com"


def ai_path(workspace: Workspace) -> str:
    return f"/workspaces/{workspace.id}/ai"


def put(client: Client, workspace: Workspace, body: dict[str, object]) -> "Response":
    return client.put(f"/api/v1{ai_path(workspace)}", body, content_type="application/json")


def preview(client: Client, workspace: Workspace, body: dict[str, object]) -> "Response":
    return post_json(client, f"{ai_path(workspace)}/preview", body)


def external(**overrides: object) -> dict[str, object]:
    return {"mode": "external", "api_key": API_KEY, "consent_address": MISTRAL} | overrides


def stored(workspace: Workspace) -> AISettings | None:
    with workspace_context(workspace.id, actor=SYSTEM):
        return AISettings.objects.first()


def events(workspace: Workspace) -> list[AIConsentEvent]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(AIConsentEvent.objects.order_by("created_at"))


@pytest.fixture
def admin(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def as_role(client: Client, workspace: Workspace, role: str) -> Client:
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=workspace, role=role)
    client.force_login(user)
    return client


# --- who may ---


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_only_an_administrator_changes_the_ai(client: Client, ws_a: Workspace, role: str) -> None:
    as_role(client, ws_a, role)

    for response in (
        put(client, ws_a, external()),
        put(client, ws_a, {"mode": "off"}),
        preview(client, ws_a, {"mode": "external"}),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"
    assert stored(ws_a) is None
    assert events(ws_a) == []


def test_a_device_token_cannot_change_the_ai(client: Client, ws_a: Workspace) -> None:
    # A stolen phone of an administrator must not send the workspace's post elsewhere.
    attacker = "https://evil.example/v1"
    headers = bearer_for(admin_of(ws_a))

    responses = (
        client.put(
            f"/api/v1{ai_path(ws_a)}",
            external(base_url=attacker, model="x", consent_address=attacker),
            content_type="application/json",
            headers=headers,
        ),
        client.post(
            f"/api/v1{ai_path(ws_a)}/preview",
            {"mode": "external", "base_url": attacker, "model": "x"},
            content_type="application/json",
            headers=headers,
        ),
    )

    for response in responses:
        assert response.status_code == 401
        assert response.json()["code"] == "not_authenticated"
    assert stored(ws_a) is None
    assert events(ws_a) == []


def test_the_contract_says_the_ai_settings_are_web_session_only() -> None:
    from brievie.api import api

    paths = api.get_openapi_schema()["paths"]
    for path, method in (
        ("/api/v1/workspaces/{workspace_id}/ai", "put"),
        ("/api/v1/workspaces/{workspace_id}/ai/preview", "post"),
    ):
        operation = paths[path][method]
        assert operation["security"] == [{"SessionAuth": []}]
        assert "Web session only" in operation["description"]


def test_another_workspace_is_not_found(admin: Client, ws_b: Workspace) -> None:
    for response in (put(admin, ws_b, external()), preview(admin, ws_b, {"mode": "external"})):
        assert response.status_code == 404
        assert response.json()["code"] == "workspace_not_found"
    assert stored(ws_b) is None


def test_changing_needs_a_login(client: Client, ws_a: Workspace) -> None:
    assert put(client, ws_a, external()).status_code == 401


# --- the notice ---


def test_preview_gives_the_notice_of_the_command_and_changes_nothing(
    admin: Client, ws_a: Workspace
) -> None:
    response = preview(admin, ws_a, {"mode": "external"})

    assert response.status_code == 200
    body = response.json()
    assert body["needs_consent"] is True
    assert body["address"] == MISTRAL
    target = settings_service.resolve(mode="external", base_url="", model="")
    assert body["notice"] == settings_service.external_notice(ws_a.name, target)
    assert "Mistral" in body["notice"]
    assert "Frankrijk" in body["notice"]
    assert stored(ws_a) is None
    assert events(ws_a) == []


def test_preview_asks_nothing_for_off_local_or_a_consent_in_force(
    admin: Client, ws_a: Workspace
) -> None:
    local: dict[str, object] = {
        "mode": "local",
        "base_url": "http://ollama:11434/v1",
        "model": "qwen",
    }
    off: dict[str, object] = {"mode": "off"}
    for body in (off, local):
        answer = preview(admin, ws_a, body).json()
        assert (answer["needs_consent"], answer["notice"]) == (False, "")

    assert put(admin, ws_a, external()).status_code == 200
    again = preview(admin, ws_a, {"mode": "external", "model": "mistral-large-latest"}).json()
    assert again["needs_consent"] is False
    other = preview(admin, ws_a, {"mode": "external", "supplier": "claude"}).json()
    assert (other["needs_consent"], other["address"]) == (True, CLAUDE)


def test_preview_refuses_settings_that_cannot_be_saved(admin: Client, ws_a: Workspace) -> None:
    response = preview(admin, ws_a, {"mode": "external", "supplier": "openai"})

    assert response.status_code == 422
    assert response.json() == {
        "code": "ai_settings_invalid",
        "message": "Vul de naam van het model in.",
    }


# --- consent ---


def test_external_without_consent_is_refused(admin: Client, ws_a: Workspace) -> None:
    response = put(admin, ws_a, {"mode": "external", "api_key": API_KEY})

    assert response.status_code == 409
    assert response.json()["code"] == "ai_consent_required"
    assert stored(ws_a) is None
    assert events(ws_a) == []


def test_a_consent_for_one_service_does_not_switch_on_another(
    admin: Client, ws_a: Workspace
) -> None:
    # The dialog showed Mistral; the request asks for Claude.
    response = put(admin, ws_a, external(supplier="claude"))

    assert response.status_code == 409
    assert response.json()["code"] == "ai_consent_required"
    assert stored(ws_a) is None

    # The same for another path behind the host that was shown.
    response = put(admin, ws_a, external(base_url="https://api.mistral.ai/v2", model="m"))
    assert response.status_code == 409
    assert stored(ws_a) is None


def test_consent_switches_on_and_records_the_administrator(admin: Client, ws_a: Workspace) -> None:
    response = put(admin, ws_a, external())

    assert response.status_code == 200
    body = response.json()
    assert (body["mode"], body["supplier"], body["host"]) == (
        "external",
        "mistral",
        "api.mistral.ai",
    )
    assert body["has_api_key"] is True
    assert body["since"] is not None
    (event,) = events(ws_a)
    assert (event.event, event.actor_id, event.actor_label) == (
        "granted", admin_of(ws_a).id, "api",
    )  # fmt: skip
    settings = stored(ws_a)
    assert settings is not None
    assert settings.consented_base_url == MISTRAL


def test_switching_off_needs_no_consent(admin: Client, ws_a: Workspace) -> None:
    put(admin, ws_a, external())

    response = put(admin, ws_a, {"mode": "off"})

    assert response.status_code == 200
    assert response.json() == {
        "mode": "off", "supplier": None, "host": None, "since": None, "has_api_key": False,
        "model": None, "auto_organise": False, "auto_confirm": False, "autopilot_allowed": True,
    }  # fmt: skip
    assert [event.event for event in events(ws_a)] == ["granted", "switched_off"]
    # Switching on again asks again.
    assert put(admin, ws_a, {"mode": "external", "api_key": API_KEY}).status_code == 409


def test_local_needs_no_consent(admin: Client, ws_a: Workspace) -> None:
    response = put(
        admin, ws_a, {"mode": "local", "base_url": "http://ollama:11434/v1", "model": "qwen"}
    )

    assert response.status_code == 200
    assert (response.json()["mode"], response.json()["host"]) == ("local", None)


# --- the key ---


def test_the_key_is_in_no_response_and_no_log_line(
    admin: Client, ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    responses = [
        preview(admin, ws_a, external()),
        put(admin, ws_a, external()),
        admin.get(f"/api/v1{ai_path(ws_a)}"),
        admin.get(f"/api/v1{ai_path(ws_a)}/suppliers"),
        # Refusals do not echo the body either.
        put(admin, ws_a, external(supplier="claude")),
        put(admin, ws_a, external(base_url="http://api.mistral.ai/v1")),
        put(admin, ws_a, {"mode": "external", "api_key": API_KEY, "model": 7}),
    ]

    assert [r.status_code for r in responses] == [200, 200, 200, 200, 409, 422, 422]
    for response in responses:
        assert API_KEY not in response.content.decode()
    assert API_KEY not in caplog.text
    settings = stored(ws_a)
    assert settings is not None
    assert API_KEY.encode() not in bytes(settings.api_key_sealed or b"")


def test_another_model_at_the_same_address_keeps_the_stored_key(
    admin: Client, ws_a: Workspace
) -> None:
    put(admin, ws_a, external())
    before = stored(ws_a)
    assert before is not None

    response = put(admin, ws_a, {"mode": "external", "model": "mistral-large-latest"})

    assert response.status_code == 200
    after = stored(ws_a)
    assert after is not None
    assert after.model == "mistral-large-latest"
    assert bytes(after.api_key_sealed or b"") == bytes(before.api_key_sealed or b"")
    assert [event.event for event in events(ws_a)] == ["granted", "changed"]


def test_a_stored_key_never_moves_to_another_address(admin: Client, ws_a: Workspace) -> None:
    put(admin, ws_a, external())

    response = put(
        admin, ws_a, {"mode": "external", "supplier": "claude", "consent_address": CLAUDE}
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "ai_settings_invalid",
        "message": "Vul de sleutel van de AI-dienst in.",
    }
    settings = stored(ws_a)
    assert settings is not None
    assert settings.base_url == MISTRAL


def test_external_without_any_key_is_refused(admin: Client, ws_a: Workspace) -> None:
    response = put(admin, ws_a, {"mode": "external", "consent_address": MISTRAL})

    assert response.status_code == 422
    assert stored(ws_a) is None


# --- settings that do not pass ---


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"mode": "local", "base_url": "https://example.com/v1", "model": "qwen"},
            "Dit adres ligt niet op je eigen netwerk.",
        ),
        (
            {"mode": "external", "base_url": "http://llm.example.com/v1", "model": "m",
             "api_key": API_KEY, "consent_address": "http://llm.example.com/v1"},
            "Het adres van een externe AI-dienst moet met https:// beginnen.",
        ),
        ({"mode": "cloud"}, "Deze AI-instelling klopt niet."),
        ({"mode": "external", "supplier": "gemini"}, "Deze AI-instelling klopt niet."),
    ],
)  # fmt: skip
def test_invalid_settings_get_a_dutch_reason(
    admin: Client, ws_a: Workspace, body: dict[str, object], message: str
) -> None:
    response = put(admin, ws_a, body)

    assert response.status_code == 422
    assert response.json() == {"code": "ai_settings_invalid", "message": message}
    assert stored(ws_a) is None


# --- the rest ---


def test_suppliers_have_a_name_and_a_country(client: Client, ws_a: Workspace) -> None:
    as_role(client, ws_a, "viewer")

    response = client.get(f"/api/v1{ai_path(ws_a)}/suppliers")

    assert response.status_code == 200
    assert response.json() == [
        {"key": "mistral", "name": "Mistral", "country": "Frankrijk (Europese Unie)",
         "host": "api.mistral.ai", "default_model": "mistral-small-latest"},
        {"key": "openai", "name": "OpenAI", "country": "Verenigde Staten",
         "host": "api.openai.com", "default_model": ""},
        {"key": "claude", "name": "Claude (Anthropic)", "country": "Verenigde Staten",
         "host": "api.anthropic.com", "default_model": "claude-opus-5-5"},
    ]  # fmt: skip


def test_changing_the_ai_is_throttled(admin: Client, ws_a: Workspace) -> None:
    codes = [put(admin, ws_a, {"mode": "off"}).status_code for _ in range(11)]

    assert codes == [200] * 10 + [429]


def test_somebody_else_cannot_use_up_the_administrators_attempts(
    admin: Client, ws_a: Workspace
) -> None:
    viewer = as_role(Client(), ws_a, "viewer")
    assert {put(viewer, ws_a, {"mode": "off"}).status_code for _ in range(12)} == {403, 429}

    assert put(admin, ws_a, {"mode": "off"}).status_code == 200


def test_status_gives_the_model_so_a_form_can_keep_it(admin: Client, ws_a: Workspace) -> None:
    put(admin, ws_a, external(model="mistral-large-latest"))

    assert admin.get(f"/api/v1{ai_path(ws_a)}").json()["model"] == "mistral-large-latest"
    put(admin, ws_a, {"mode": "off"})
    assert admin.get(f"/api/v1{ai_path(ws_a)}").json()["model"] is None


def test_the_notice_shows_the_whole_address_when_it_is_not_the_presets(
    admin: Client, ws_a: Workspace
) -> None:
    address = "https://api.mistral.ai:8443/elders"

    notice = preview(admin, ws_a, {"mode": "external", "base_url": address, "model": "m"}).json()

    assert notice["address"] == address
    assert address in notice["notice"]
    assert "Frankrijk" not in notice["notice"]


@pytest.mark.parametrize(
    "address",
    [
        "http://169.254.169.254/v1",  # link-local: where a cloud keeps its metadata
        "http://[fe80::1]/v1",
        "http://[2002:808:808::1]/v1",  # 6to4: a public IPv4 address inside
        "http://[2001:0:808:808::1]/v1",  # Teredo
        "http://[::ffff:8.8.8.8]/v1",
    ],
)
def test_local_refuses_addresses_that_only_look_private(
    admin: Client, ws_a: Workspace, address: str
) -> None:
    response = put(admin, ws_a, {"mode": "local", "base_url": address, "model": "qwen"})

    assert response.status_code == 422
    assert response.json()["message"] == "Dit adres ligt niet op je eigen netwerk."
    assert stored(ws_a) is None


def test_new_fields_are_optional_in_the_contract() -> None:
    from brievie.api import api

    schemas = api.get_openapi_schema()["components"]["schemas"]

    assert schemas["AiStatusOut"]["required"] == ["mode"]
    assert "has_api_key" in schemas["AiStatusOut"]["properties"]
    assert schemas["AiSettingsIn"]["required"] == ["mode"]
