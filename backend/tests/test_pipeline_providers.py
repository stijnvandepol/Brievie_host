import json

import httpx
import pytest

from apps.common import crypto
from apps.pipeline import settings_service
from apps.pipeline.errors import PermanentError, RetryableError
from apps.pipeline.models import AISettings
from apps.pipeline.providers import (
    MAX_TEXT_CHARS,
    OpenAICompatibleProvider,
    ProviderRejected,
    ProviderUnavailable,
)
from apps.pipeline.schema import json_schema
from apps.workspaces.context import workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

API_KEY = "sk-geheime-sleutel-123"


def provider(handler: httpx.MockTransport, **kwargs: object) -> OpenAICompatibleProvider:
    options: dict[str, object] = {
        "base_url": "https://api.mistral.ai/v1/",
        "api_key": API_KEY,
        "model": "mistral-small-latest",
    }
    options.update(kwargs)
    return OpenAICompatibleProvider(transport=handler, **options)  # type: ignore[arg-type]


def answering(
    content: object, status: int = 200
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {"choices": [{"message": {"content": content}}]} if status == 200 else {"x": 1}
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle), seen


def test_request_asks_for_the_schema_at_temperature_zero() -> None:
    transport, seen = answering('{"a": 1}')

    result = provider(transport).extract("De brief")

    assert result == '{"a": 1}'
    (request,) = seen
    assert str(request.url) == "https://api.mistral.ai/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    body = json.loads(request.content)
    assert body["model"] == "mistral-small-latest"
    assert body["temperature"] == 0
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "brievie_extraction", "schema": json_schema(), "strict": True},
    }
    system, user = body["messages"]
    assert system["role"] == "system"
    assert "samenvatting_b1" in system["content"]
    assert user == {"role": "user", "content": "De brief"}


def test_no_authorization_header_without_a_key() -> None:
    transport, seen = answering("{}")

    provider(transport, api_key=None, base_url="http://ollama:11434/v1").extract("x")

    assert "Authorization" not in seen[0].headers
    assert str(seen[0].url) == "http://ollama:11434/v1/chat/completions"


def test_provider_name_is_the_host() -> None:
    transport, _ = answering("{}")
    assert provider(transport).name == "api.mistral.ai"
    assert provider(transport).model == "mistral-small-latest"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_passing_failures_are_retryable(status: int) -> None:
    transport, _ = answering("", status=status)

    with pytest.raises(ProviderUnavailable) as excinfo:
        provider(transport).extract("x")

    assert isinstance(excinfo.value, RetryableError)
    assert excinfo.value.code == "ai_unavailable"


def test_timeout_and_connection_errors_are_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("te laat", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("geen verbinding", request=request)

    for handler in (timeout, refused):
        with pytest.raises(ProviderUnavailable):
            provider(httpx.MockTransport(handler)).extract("x")


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "ai_key_rejected"), (403, "ai_key_rejected"), (400, "ai_request_rejected"),
     (404, "ai_request_rejected"), (422, "ai_request_rejected")],
)  # fmt: skip
def test_refusals_are_permanent(status: int, code: str) -> None:
    transport, _ = answering("", status=status)

    with pytest.raises(ProviderRejected) as excinfo:
        provider(transport).extract("x")

    assert isinstance(excinfo.value, PermanentError)
    assert excinfo.value.code == code


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {}}]}, [1]])
def test_answer_without_content_is_invalid_output(body: object) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    with pytest.raises(PermanentError) as excinfo:
        provider(transport).extract("x")

    assert excinfo.value.code == "ai_invalid_output"


def test_key_and_letter_never_reach_an_exception_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad key {API_KEY} for: geheime brieftekst")

    with caplog.at_level("DEBUG"), pytest.raises(ProviderRejected) as excinfo:
        provider(httpx.MockTransport(echo)).extract("geheime brieftekst")

    for text in (str(excinfo.value), repr(excinfo.value), caplog.text):
        assert API_KEY not in text
        assert "geheime brieftekst" not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_long_text_is_cut_at_a_page_boundary() -> None:
    transport, seen = answering("{}")
    pages = ["a" * 12000, "b" * 12000, "c" * 12000, "d" * 12000]

    provider(transport).extract("\n\f\n".join(pages))

    sent = json.loads(seen[0].content)["messages"][1]["content"]
    assert sent == "\n\f\n".join(pages[:2])
    assert MAX_TEXT_CHARS == 30000


def test_one_very_long_page_is_cut_hard() -> None:
    transport, seen = answering("{}")

    provider(transport).extract("a" * 40000)

    assert len(json.loads(seen[0].content)["messages"][1]["content"]) == 30000


def test_provider_for_is_none_when_ai_is_off(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        assert settings_service.provider_for(ws_a.id) is None
        settings_service.configure(
            workspace_id=ws_a.id, mode="off", base_url="", model="", api_key=None
        )
        assert settings_service.provider_for(ws_a.id) is None


def test_configure_external_seals_the_key_and_builds_the_provider(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", base_url="", model="", api_key=API_KEY
        )
        stored = AISettings.objects.get()
        sealed = bytes(stored.api_key_sealed or b"")
        built = settings_service.provider_for(ws_a.id)
        opened = crypto.open_sealed(workspace_key(ws_a.id), sealed, aad=b"ai-api-key")

    assert (stored.base_url, stored.model) == ("https://api.mistral.ai/v1", "mistral-small-latest")
    assert API_KEY.encode() not in sealed
    assert opened == API_KEY.encode()
    assert isinstance(built, OpenAICompatibleProvider)
    assert built.name == "api.mistral.ai"


def test_configure_again_replaces_the_settings(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", base_url="", model="", api_key=API_KEY
        )
        settings_service.configure(
            workspace_id=ws_a.id, mode="off", base_url="", model="", api_key=None
        )
        stored = AISettings.objects.get()

    assert stored.mode == "off"
    assert stored.api_key_sealed is None


def test_external_needs_https_and_a_key(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        with pytest.raises(ValueError, match="https"):
            settings_service.configure(
                workspace_id=ws_a.id, mode="external", base_url="http://api.mistral.ai/v1",
                model="m", api_key=API_KEY,
            )  # fmt: skip
        with pytest.raises(ValueError, match="key"):
            settings_service.configure(
                workspace_id=ws_a.id, mode="external", base_url="", model="", api_key=None
            )
        assert not AISettings.objects.exists()


@pytest.mark.parametrize(
    "url",
    ["http://ollama:11434/v1", "http://192.168.1.20:11434/v1", "http://localhost:11434/v1",
     "http://127.0.0.1:11434/v1", "https://nas.local/v1", "http://[::1]:11434/v1",
     "http://10.0.0.5/v1"],
)  # fmt: skip
def test_local_accepts_addresses_on_the_own_network(ws_a: Workspace, url: str) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url=url, model="qwen", api_key=None
        )
        assert settings_service.provider_for(ws_a.id) is not None


@pytest.mark.parametrize(
    "url",
    ["https://api.mistral.ai/v1", "http://8.8.8.8/v1", "http://example.com/v1", "",
     "ftp://ollama/v1", "http://[2a00:1450:4001::200e]/v1"],
)  # fmt: skip
def test_local_refuses_addresses_outside_the_own_network(ws_a: Workspace, url: str) -> None:
    # Spec acceptance: no document content leaves the server with AI `local`.
    with workspace_context(ws_a.id), pytest.raises(ValueError, match="network"):
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url=url, model="qwen", api_key=None
        )


def test_local_is_checked_again_before_use(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        AISettings.objects.create(
            workspace=ws_a, mode="local", base_url="https://api.mistral.ai/v1", model="m"
        )
        with pytest.raises(PermanentError) as excinfo:
            settings_service.provider_for(ws_a.id)

    assert excinfo.value.code == "ai_not_configured"


def test_unknown_mode_is_refused(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id), pytest.raises(ValueError, match="mode"):
        settings_service.configure(
            workspace_id=ws_a.id, mode="cloud", base_url="", model="", api_key=None
        )
