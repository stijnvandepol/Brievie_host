import json

import anthropic
import httpx2
import pytest

from apps.pipeline import settings_service
from apps.pipeline.errors import PermanentError, RetryableError
from apps.pipeline.models import AISettings
from apps.pipeline.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    ProviderRejected,
    ProviderUnavailable,
)
from apps.pipeline.schema import json_schema
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

API_KEY = "nep-sleutel-voor-tests"
LETTER = "geheime brieftekst"


def message(stop_reason: str = "end_turn", content: list[dict[str, str]] | None = None) -> dict:  # type: ignore[type-arg]
    blocks = content if content is not None else [{"type": "text", "text": '{"a": 1}'}]
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def provider(handler: httpx2.MockTransport, **kwargs: object) -> AnthropicProvider:
    options: dict[str, object] = {
        "base_url": "https://api.anthropic.com",
        "api_key": API_KEY,
        "model": "claude-opus-5-5",
    }
    options.update(kwargs)
    client = anthropic.DefaultHttpxClient(transport=handler)
    return AnthropicProvider(http_client=client, **options)  # type: ignore[arg-type]


def answering(body: object, status: int = 200) -> tuple[httpx2.MockTransport, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, json=body)

    return httpx2.MockTransport(handle), seen


def test_request_asks_for_the_schema_without_sampling_options() -> None:
    transport, seen = answering(message())

    result = provider(transport).extract(LETTER)

    assert result == '{"a": 1}'
    (request,) = seen
    assert request.url.path == "/v1/messages"
    # SDK 2.x sends the API key as a bearer token.
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 16000
    assert "samenvatting_b1" in body["system"]
    assert body["messages"] == [{"role": "user", "content": LETTER}]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": json_schema()}}
    for forbidden in ("temperature", "thinking", "tool_choice", "output_format"):
        assert forbidden not in body


def test_first_text_block_is_returned() -> None:
    blocks = [
        {"type": "thinking", "thinking": "hm", "signature": "s"},
        {"type": "text", "text": "{}"},
    ]
    transport, _ = answering(message(content=blocks))

    assert provider(transport).extract("x") == "{}"


def test_provider_name_is_the_host() -> None:
    transport, _ = answering(message())
    built = provider(transport)
    assert built.name == "api.anthropic.com"
    assert built.model == "claude-opus-5-5"


def test_long_text_is_cut() -> None:
    transport, seen = answering(message())

    provider(transport).extract("a" * 40000)

    assert len(json.loads(seen[0].content)["messages"][0]["content"]) == 30000


@pytest.mark.parametrize("status", [429, 500, 503, 529, 408, 409])
def test_passing_failures_are_retryable(status: int) -> None:
    transport, _ = answering({"type": "error", "error": {"type": "x", "message": "m"}}, status)

    with pytest.raises(ProviderUnavailable) as excinfo:
        provider(transport).extract("x")

    assert isinstance(excinfo.value, RetryableError)
    assert excinfo.value.code == "ai_unavailable"


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "ai_key_rejected"), (403, "ai_key_rejected"), (400, "ai_request_rejected"),
     (404, "ai_request_rejected"), (422, "ai_request_rejected")],
)  # fmt: skip
def test_refusals_are_permanent(status: int, code: str) -> None:
    transport, _ = answering({"type": "error", "error": {"type": "x", "message": "m"}}, status)

    with pytest.raises(ProviderRejected) as excinfo:
        provider(transport).extract("x")

    assert isinstance(excinfo.value, PermanentError)
    assert excinfo.value.code == code


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_answer_that_did_not_end_normally_is_invalid_output(stop_reason: str) -> None:
    transport, _ = answering(message(stop_reason=stop_reason))

    with pytest.raises(PermanentError) as excinfo:
        provider(transport).extract("x")

    assert excinfo.value.code == "ai_invalid_output"


def test_answer_without_a_text_block_is_invalid_output() -> None:
    transport, _ = answering(message(content=[]))

    with pytest.raises(PermanentError) as excinfo:
        provider(transport).extract("x")

    assert excinfo.value.code == "ai_invalid_output"


def test_connection_errors_and_timeouts_are_retryable() -> None:
    def timeout(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("te laat", request=request)

    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("geen verbinding", request=request)

    for handler in (timeout, refused):
        with pytest.raises(ProviderUnavailable):
            provider(httpx2.MockTransport(handler)).extract("x")


def test_key_and_letter_never_reach_an_exception_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def echo(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error",
                                             "message": f"bad key {API_KEY} for: {LETTER}"}},
        )  # fmt: skip

    with caplog.at_level("DEBUG"), pytest.raises(ProviderRejected) as excinfo:
        provider(httpx2.MockTransport(echo)).extract(LETTER)

    for text in (str(excinfo.value), repr(excinfo.value), caplog.text):
        assert API_KEY not in text
        assert LETTER not in text
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_configure_claude_stores_the_preset_and_builds_the_provider(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="claude", base_url="", model="",
            api_key=API_KEY,
        )  # fmt: skip
        stored = AISettings.objects.get()
        built = settings_service.provider_for(ws_a.id)

    assert (stored.api, stored.base_url, stored.model) == (
        "anthropic", "https://api.anthropic.com", "claude-opus-5-5",
    )  # fmt: skip
    assert isinstance(built, AnthropicProvider)
    assert built.name == "api.anthropic.com"


def test_claude_model_can_be_overridden(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="claude", base_url="",
            model="claude-haiku-5-5", api_key=API_KEY,
        )  # fmt: skip
        stored = AISettings.objects.get()

    assert (stored.api, stored.model) == ("anthropic", "claude-haiku-5-5")


def test_claude_still_needs_https_and_a_key(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        with pytest.raises(ValueError, match="https"):
            settings_service.configure(
                workspace_id=ws_a.id, mode="external", supplier="claude",
                base_url="http://api.anthropic.com", model="", api_key=API_KEY,
            )  # fmt: skip
        with pytest.raises(ValueError, match="key"):
            settings_service.configure(
                workspace_id=ws_a.id, mode="external", supplier="claude", base_url="",
                model="", api_key=None,
            )  # fmt: skip
        assert not AISettings.objects.exists()


def test_openai_needs_a_model_name(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        with pytest.raises(ValueError, match="needs a model name"):
            settings_service.configure(
                workspace_id=ws_a.id, mode="external", supplier="openai", base_url="",
                model="", api_key=API_KEY,
            )  # fmt: skip
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="openai", base_url="",
            model="some-model", api_key=API_KEY,
        )  # fmt: skip
        stored = AISettings.objects.get()
        built = settings_service.provider_for(ws_a.id)

    assert (stored.api, stored.base_url, stored.model) == (
        "openai", "https://api.openai.com/v1", "some-model",
    )  # fmt: skip
    assert isinstance(built, OpenAICompatibleProvider)


def test_custom_url_with_mistral_stays_openai_compatible(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="mistral",
            base_url="https://llm.example.com/v1", model="m", api_key=API_KEY,
        )  # fmt: skip
        stored = AISettings.objects.get()

    assert (stored.api, stored.base_url) == ("openai", "https://llm.example.com/v1")


def test_unknown_supplier_is_refused(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id), pytest.raises(ValueError, match="supplier"):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="gemini", base_url="", model="",
            api_key=API_KEY,
        )  # fmt: skip


def test_mode_off_and_local_reset_the_api(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="claude", base_url="", model="",
            api_key=API_KEY,
        )  # fmt: skip
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url="http://ollama:11434/v1", model="q",
            api_key=None,
        )  # fmt: skip
        assert AISettings.objects.get().api == "openai"
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", supplier="claude", base_url="", model="",
            api_key=API_KEY,
        )  # fmt: skip
        settings_service.configure(
            workspace_id=ws_a.id, mode="off", base_url="", model="", api_key=None
        )
        assert AISettings.objects.get().api == "openai"


def test_default_supplier_is_still_mistral(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", base_url="", model="", api_key=API_KEY
        )
        stored = AISettings.objects.get()

    assert (stored.api, stored.base_url, stored.model) == (
        "openai", "https://api.mistral.ai/v1", "mistral-small-latest",
    )  # fmt: skip
