"""The AI services that read a letter. One interface, with a fake in the tests."""

import logging
from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import anthropic
import httpx

from apps.pipeline.choices import ExtractionRequest, choices_block
from apps.pipeline.errors import PermanentError, PipelineError, RetryableError
from apps.pipeline.schema import json_schema

# More text than this is cut off (at a page boundary when possible).
MAX_TEXT_CHARS = 30000
# Claude may reason before it answers; the answer itself is a small JSON object.
_MAX_OUTPUT_TOKENS = 16000
_PAGE_BREAK = "\f"
# The SDK logs whole request bodies (the letter) at DEBUG; keep that out of every log.
logging.getLogger("anthropic").setLevel(logging.WARNING)
_PROMPT = Path(__file__).parent / "prompts" / "extract_nl.txt"


class ProviderUnavailable(RetryableError):  # noqa: N818 - named for what happened
    """The service did not answer, or asked us to come back later."""

    def __init__(self) -> None:
        super().__init__("ai_unavailable")


class ProviderRejected(PermanentError):  # noqa: N818
    """The service refused the request; trying again will not help."""


class LLMProvider(Protocol):
    name: str
    model: str

    def extract(self, request: ExtractionRequest) -> str:
        """The model's answer for the letter in `request`: a JSON string."""
        ...


class OpenAICompatibleProvider:
    """Any service that speaks the OpenAI chat-completions API: Mistral, Ollama, ..."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self.name = urlsplit(base_url).hostname or "unknown"
        self.model = model

    def extract(self, request: ExtractionRequest) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_message(request)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "brievie_extraction",
                    "schema": json_schema(request),
                    "strict": True,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        # Errors are raised `from None`: httpx's own exceptions and the response body
        # can carry the key or the letter, and must not travel with ours.
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError:
            raise ProviderUnavailable from None
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable
        if response.status_code in (401, 403):
            raise ProviderRejected("ai_key_rejected")
        if response.status_code != 200:
            raise ProviderRejected("ai_request_rejected")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise PermanentError("ai_invalid_output") from None
        if not isinstance(content, str):
            raise PermanentError("ai_invalid_output")
        return content


class AnthropicProvider:
    """Claude, through the official SDK: the Messages API is not OpenAI-compatible."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 120.0,
        http_client: anthropic.DefaultHttpxClient | None = None,
    ) -> None:
        # The SDK's own client follows redirects: a redirect would carry the letter to a
        # host nobody agreed to. The OpenAI-compatible client does not follow them either.
        http_client = http_client or anthropic.DefaultHttpxClient(follow_redirects=False)
        # The job queue retries, not the SDK.
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
        self.name = urlsplit(base_url).hostname or "unknown"
        self.model = model

    def extract(self, request: ExtractionRequest) -> str:
        # The error is built inside the handler but raised outside it, `from None`: the
        # SDK's exceptions and the response body can carry the key or the letter and
        # must not travel with ours, not even as implicit context.
        error: PipelineError
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_system_prompt(),
                messages=[{"role": "user", "content": _user_message(request)}],
                output_config={"format": {"type": "json_schema", "schema": json_schema(request)}},
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            error = ProviderRejected("ai_key_rejected")
        except anthropic.RateLimitError:
            error = ProviderUnavailable()
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409):
                error = ProviderUnavailable()
            else:
                error = ProviderRejected("ai_request_rejected")
        except anthropic.APIConnectionError:
            error = ProviderUnavailable()
        else:
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if block.type == "text":
                        return block.text
            error = PermanentError("ai_invalid_output")
        raise error from None


@cache
def _system_prompt() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def _user_message(request: ExtractionRequest) -> str:
    """The letter, cut to size, and under it the lists to choose from (never cut)."""
    block = choices_block(request)
    letter = _cut(request.text)
    return f"{letter}\n\n{block}" if block else letter


def _cut(text: str) -> str:
    """At most MAX_TEXT_CHARS: whole pages while they fit, else a hard cut."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    kept = text[:MAX_TEXT_CHARS]
    boundary = kept.rfind(_PAGE_BREAK)
    if boundary <= 0:
        return kept
    return kept[:boundary].rstrip("\n")
